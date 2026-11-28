"""v1.1: expanded, opt-in mailbox organiser for Gmail and Yahoo."""
import csv
import email
import imaplib
import json
import logging
import os
import re
import ssl
import tempfile
import hashlib
import html
from urllib.parse import urlparse
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
import requests

BASE = Path('/config')
OPTS = Path('/data/options.json')
REPORT_DIR = BASE / 'reports'
PREFERENCES = BASE / 'sender_preferences.json'
EXPANDED_PREFS = BASE / 'expanded_sender_preferences.json'
EXPANDED_POLICY = BASE / 'expanded_senders.json'
LOG = logging.getLogger('email_organiser')
GMAIL_READ_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
GMAIL_MODIFY_SCOPE = 'https://www.googleapis.com/auth/gmail.modify'
AUDIT_FILE = BASE / 'audit.jsonl'
BACKLOG_PROGRESS = BASE / 'backlog_progress.json'


def save_backlog_progress(account, page):
    """Persist scan position without storing message contents or identifiers."""
    data=read_json(BASE / 'backlog_progress.json',{})
    if not isinstance(data,dict):
        data={}
    data[account]=max(0,int(page))
    BASE.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=BASE,prefix='.backlog-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(data,handle)
        os.chmod(tmp,0o600)
        os.replace(tmp,BASE / 'backlog_progress.json')
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

LABELS = {'Purchases': 'EO Purchases', 'Delivery Updates': 'EO Low Priority', 'Low Priority': 'EO Low Priority', 'Review': 'EO Review', 'Newsletters': 'EO Review', 'Promotions': 'EO Promotions'}
VALID_PREFS = {'Important', 'Transactions', 'Purchases', 'Delivery Updates', 'Purchase Issues', 'Review', 'Low Priority', 'Promotions', 'Newsletters', 'Suspicious'}
FIELDS = ['message_id', 'sender', 'subject', 'category', 'confidence_heuristic', 'reason', 'unsubscribe_header_present', 'list_header_present', 'auth_result', 'proposed_action', 'planned_action', 'actual_action']


def read_json(path, fallback=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return fallback if fallback is not None else {}


def decoded(value):
    try:
        return str(make_header(decode_header(value or '')))
    except Exception:
        return str(value or '')


def address(value):
    return parseaddr(value or '')[1].strip().lower()


def preferences():
    data = read_json(PREFERENCES, {})
    if not isinstance(data, dict):
        return {'senders': {}, 'domains': {}}
    result = {}
    for key in ('senders', 'domains'):
        entries = data.get(key, {})
        result[key] = {str(k).lower().strip(): v for k,v in entries.items()
                       if isinstance(v,str) and v in VALID_PREFS} if isinstance(entries,dict) else {}
    # Separate reviewed expansion preferences from existing user preferences.
    # Existing explicit user preferences take precedence.
    expanded = read_json(EXPANDED_PREFS, {})
    for key in ('senders', 'domains'):
        entries=expanded.get(key,{}) if isinstance(expanded,dict) else {}
        if isinstance(entries,dict):
            for k,v in entries.items():
                if key=='senders' and isinstance(v,str) and v in VALID_PREFS:
                    result[key].setdefault(str(k).lower().strip(),v)
    return result


def flags(headers):
    """Input: lowercased header map. Authentication-Results is only a *hint*.

    Header text may be spoofed; do not trust it to allow irreversible actions.
    """
    return {
        'unsub': bool(headers.get('list-unsubscribe')),
        'oneclick': 'list-unsubscribe=one-click' in headers.get('list-unsubscribe-post','').lower(),
        'unsubscribe_urls': re.findall(r'<(https://[^>\s]+)>', headers.get('list-unsubscribe',''), re.I),
        'list': bool(headers.get('list-id') or headers.get('list-unsubscribe') or
                     headers.get('precedence','').lower() in ('bulk', 'list')),
        'auto': headers.get('auto-submitted','no').lower() not in ('no',''),
        'auth': 'reported-pass' if re.search(r'\b(dkim|spf|dmarc)\s*=\s*pass\b', headers.get('authentication-results',''),re.I)
                else 'not-established',
    }


def normalised_subject(value):
    """Normalise compatibility characters and strip obfuscating format characters.

    Never use this transformation to authorise deletions or unsubscribe requests.
    """
    folded = unicodedata.normalize('NFKC', value or '').casefold()
    def hidden(ch):
        return (unicodedata.category(ch) == 'Cf' or 0xE0000 <= ord(ch) <= 0xE007F
                or 0xFE00 <= ord(ch) <= 0xFE0F)
    # Remove injection-like hidden characters and internal tabs; keep real spaces.
    return re.sub(r' +', ' ', ''.join(ch for ch in folded if not hidden(ch) and ch not in '\t\r\n')).strip()


def commercial_sender_subject(msg):
    """Two independent clues: commercial sender display name and promotional subject.

    This is deliberately narrower than matching a single generic subject word.
    Protect transaction/security and purchase-exception subjects separately.
    """
    display_name=parseaddr(msg.get('sender',''))[0].casefold()
    subject=normalised_subject(msg.get('subject',''))
    commercial_name=re.search(
        r'\\b(?:shop|store|outlet|publishing|photobook|accessories|'
        r'gear|deals|retail|fashion|eyewear|catalogue)\\b',
        display_name
    )
    marketing_subject=(
        'introducing', 'just landed', 'has landed', 'have arrived',
        'is here', 'are here', 'new arrivals', 'new collection',
        'new 202', 'your cart', 'saved on shop', 'didn\'t want you to miss',
        'didn’t want you to miss', 'weekend project', 'more than a charm',
        'new product', 'new gear', 'exclusive offer',
    )
    sensitive=(
        'account', 'password', 'verification', 'security', 'invoice',
        'refund', 'payment', 'receipt', 'your order', 'delivery',
        'tracking', 'shipment', 'return', 'cancelled', 'canceled',
        'policy', 'appointment', 'statement',
    )
    return (
        bool(commercial_name)
        and any(term in subject for term in marketing_subject)
        and not any(term in subject for term in sensitive)
        and not any(unicodedata.category(ch)=='Cf' for ch in msg.get('subject',''))
    )


def classify(msg, prefs):
    """Classification suggestions only. Numeric scores are uncalibrated heuristics."""
    sender = address(msg['sender'])
    domain = sender.rsplit('@', 1)[-1] if '@' in sender else ''
    raw = msg['subject']
    s = normalised_subject(raw)
    f = msg['flags']
    pref = prefs['senders'].get(sender) or prefs['domains'].get(domain)

    security_terms = (
        'app password', 'password was', 'password reset', 'password changed',
        '2fa', 'two-factor', 'two factor', 'verification code', 'verify your email',
        'verify your account', 'security alert', 'sign-in', 'sign in', 'signed in',
        'login code', 'account recovery', 'account access', 'account data',
        'shared some google account data', 'family sharing', 'identity verification',
        'subscription is confirmed', 'subscription confirmed', 'account was updated',
        'account has been updated', 'changes to your account', 'account on meta apps',
        'action required', 'registration and insurance details', 'account confirmation'
    )
    transaction_terms = (
        'invoice', 'receipt', 'order confirmation', 'order confirmed', 'ordered:',
        'order update', 'out for delivery', 'delivered:', 'shipped:', 'shipment',
        'tracking number', 'parcel', 'delivery is scheduled', 'we\'ll be there in',
        'we’ll be there in', 'booking', 'flight update', 'update on your flight',
        'payment received', 'payment due', 'billing statement', 'renewal summary',
        'renewal notice', 'policy renewal', 'insurance renewal', 'appointment confirmation',
        'refund processed', 'tax invoice', 'submission is complete',
        'developer support', 'app review'
    )
    marketing_terms = (
        '% off', 'half price', 'discount', 'flash sale', 'clearance',
        'black friday', 'coupon', 'sale ends', 'sale is on', 'shop now',
        'promo code', 'free shipping', 'limited time offer', 'buy now',
        'exclusive deal', 'special offer', 'last chance', 'up to 75%',
        'free gift', 'giveaway', 'offer ends', 'biggest sale',
        'subscriber access', 'deal season', 'reward from paypal',
        'sitewide offer', 'picked these out', 'get connected with our best',
        'free microfibre', 'prices to ', 'price drop', 'flights now from',
        'prices are lowering', 'new product', 'meet surface laptop',
        'new arrivals', 'shop the', 'holiday rush', 'your website needs'
    )
    notification_terms = (
        'daily twintip leaderboard', 'weekly twintip leaderboard',
        'monthly twintip leaderboard', 'weekly summary', 'daily digest',
        'new follower', 'liked your', 'monthly stats', 'weekly stats'
    )
    newsletter_terms = ('newsletter', 'weekly roundup', 'morning briefing',
                        'daily digest', 'weekly digest', 'going digital #')

    # User-defined purchase policy (audit only): purchase records should be retained
    # outside the main Inbox, routine delivery updates should not create Inbox work,
    # and problems/refunds must remain visible. Subjects and apparent sender domains
    # are unverified; these recommendations NEVER authorise a mailbox mutation.
    purchase_problem_terms = (
        'payment failed', 'payment declined', 'payment problem', 'payment unsuccessful',
        'order cancelled', 'order canceled', 'refund', 'return approved',
        'problem with your order', 'issue with your order', 'delivery exception',
        'unable to deliver', 'delivery failed', 'order delayed', 'action required on your order'
    )
    purchase_record_terms = (
        'ordered:', 'order confirmation', 'order confirmed', 'your order has been placed',
        'thanks for your order', 'your receipt', 'tax invoice', 'purchase confirmation'
    )
    delivery_terms = (
        'out for delivery', 'shipped:', 'delivered:', 'your delivery is scheduled',
        'delivery is scheduled', 'your parcel is here', 'parcel is here',
        'we have received your amazon parcel', "we've received your amazon parcel",
        'we’ll be there in', "we'll be there in", 'shipment tracking',
        'your amazon delivery', 'tracking update'
    )
    # Sender strings are unverified, so only user-approved exact addresses may be moved.
    shopping_sender = domain in ('amazon.com.au', 'dragonflyinternational.com', 'ebay.com.au', 'etsy.com', 'kogan.com') or domain.endswith('.dragonflyinternational.com')
    if any(t in s for t in security_terms) and any(t in s for t in ('password', 'security', 'verify', 'verification', 'account', 'action required', '2fa', 'sign-in')):
        return 'Important', .90, 'Account/security subject; protect', 'KEEP IN INBOX'
    if any(t in s for t in purchase_problem_terms) and (shopping_sender or 'order' in s or 'delivery' in s):
        return 'Purchase Issues', .80, 'Possible purchase exception; retain for human attention', 'KEEP IN INBOX / REVIEW ISSUE'
    # Order confirmations are explicit purchase records, even for unfamiliar shops.
    if any(t in s for t in purchase_record_terms) and (shopping_sender or 'order' in s or 'receipt' in s or 'invoice' in s):
        return 'Purchases', .80, 'Purchase proof; proposed Purchases folder (no action)', 'PURCHASES FOLDER - PROPOSED ONLY'
    if any(t in s for t in delivery_terms) and shopping_sender:
        return 'Delivery Updates', .78, 'Routine parcel notification; archive candidate only', 'ARCHIVE DELIVERY UPDATE - PROPOSED ONLY'
    # Also recognise explicit courier updates without matching a merchant sender.
    if any(t in s for t in ('out for delivery', 'delivered:', 'shipped:')):
        return 'Delivery Updates', .68, 'Delivery status; verify origin before any action', 'DELIVERY REVIEW - PROPOSED ONLY'

    # Avoid calling a marketing pitch a real invoice or insurance transaction.
    # Clear sale terms win over broad financial nouns, never over account/security notices.
    if any(t in s for t in security_terms):
        return 'Important', .90, 'Account/security or action-required subject; keep for review', 'KEEP IN INBOX'
    if any(t in s for t in transaction_terms):
        return 'Transactions', .86, 'Order, payment, booking or delivery subject; protective rule', 'KEEP IN INBOX'

    # Hidden Unicode format characters alongside financial/marketing pitches are
    # a notable warning sign, but not proof of malicious intent.
    format_chars = sum((unicodedata.category(ch) == 'Cf' or 0xE0000 <= ord(ch) <= 0xE007F
                        or 0xFE00 <= ord(ch) <= 0xFE0F) for ch in raw)
    if format_chars >= 2 and any(t in s for t in (
        'loan', 'credit', 'insurance', 'funds', 'eligible', 'quote', 'consultation',
        'medication', 'retirement', 'income', 'heloc', 'congratulations',
        'card bill', 'available now', 'debt', 'free installation')):
        return 'Suspicious', .72, 'Obfuscated Unicode in financial/solicitation subject; investigate', 'SUSPICIOUS REVIEW - NEVER UNSUBSCRIBE'

    # Gmail's built-in category is stronger evidence of commercial mail than
    # our keyword guesses. Subject-level protections above still take priority.
    gmail_labels=set(msg.get('gmail_labels',[]))
    if ('CATEGORY_PROMOTIONS' in gmail_labels
            and 'STARRED' not in gmail_labels
            and 'CATEGORY_PERSONAL' not in gmail_labels):
        return 'Promotions', .92, 'Gmail Promotions category', 'MOVE TO EO PROMOTIONS - REVERSIBLE'

    if commercial_sender_subject(msg):
        return 'Promotions', .78, 'Commercial sender and promotional subject', 'MOVE TO EO PROMOTIONS - REVERSIBLE'

    if pref:
        proposed = {'Important':'KEEP IN INBOX','Transactions':'KEEP IN INBOX',
                    'Purchases':'PURCHASES FOLDER - PROPOSED ONLY', 'Delivery Updates':'ARCHIVE DELIVERY UPDATE - PROPOSED ONLY', 'Purchase Issues':'KEEP IN INBOX / REVIEW ISSUE', 'Review':'REVIEW','Low Priority':'LOW PRIORITY REVIEW',
                    'Promotions':'PROMOTION REVIEW','Newsletters':'NEWSLETTER REVIEW',
                    'Suspicious':'SUSPICIOUS REVIEW - NEVER UNSUBSCRIBE'}[pref]
        return pref, .94, 'Explicit sender/domain preference; subject protections take priority', proposed

    if any(t in s for t in marketing_terms):
        return 'Promotions', (.83 if f['list'] else .68), (
            'Marketing subject with mailing-list signal' if f['list']
            else 'Marketing subject; mailing-list legitimacy not established'), 'PROMOTION REVIEW / POSSIBLE UNSUBSCRIBE' if f['unsub'] and f['list'] else 'PROMOTION REVIEW - NO AUTO UNSUBSCRIBE'
    if any(t in s for t in notification_terms):
        return 'Low Priority', .75, 'Routine non-urgent activity notification', 'LOW PRIORITY REVIEW'
    if f['list'] or any(t in s for t in newsletter_terms):
        return 'Newsletters', .64, 'Newsletter/list evidence; sender intent not fully verified', 'NEWSLETTER REVIEW'
    return 'Review', .20, 'Insufficient evidence; no automatic changes', 'REVIEW'


def atomic_csv(path, columns, rows):
    REPORT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(REPORT_DIR,0o700)
    fd,tmp = tempfile.mkstemp(dir=REPORT_DIR,prefix='.tmp-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=columns,extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_reports(account, messages, prefs, preview_policy=None, actual_outcomes=None):
    preview_policy=preview_policy or {}
    actual_outcomes=actual_outcomes or {}
    message_rows=[]
    by_sender=defaultdict(list)
    counts=Counter()
    for msg in messages:
        cat,score,why,proposal=classify(msg,prefs)
        sender=address(msg['sender']) or '(unknown sender)'
        row={'message_id':msg['id'], 'sender':sender, 'subject':msg['subject'],
             'category':cat,'confidence_heuristic':f'{score:.2f}',
             'reason':why,'unsubscribe_header_present':'yes' if msg['flags']['unsub'] else 'no',
             'list_header_present':'yes' if msg['flags']['list'] else 'no',
             'auth_result':msg['flags']['auth'],
             'proposed_action':proposal,
             'planned_action': operation_description(proposed_operation(msg,cat,'safe',preview_policy,{'enable_actions':True,'trash_old_delivery_updates':False})),
             'actual_action': actual_outcomes.get(str(msg['id']),'NO ACTION')}
        message_rows.append(row)
        by_sender[sender].append(row)
        counts[cat]+=1
    senders=[]
    for sender,items in sorted(by_sender.items()):
        categories=Counter(x['category'] for x in items)
        # protect mixed transactional and marketing senders in the summary
        protective=bool(set(categories) & {'Important','Transactions'})
        mixed=len(categories)>1
        suggested=('REVIEW MIXED SENDER' if mixed else items[0]['proposed_action'])
        senders.append({'sender':sender,'messages':len(items),
                        'categories':'; '.join(f'{k}:{n}' for k,n in sorted(categories.items())),
                        'contains_important_or_transactional':'yes' if protective else 'no',
                        'unsubscribe_header_present':'yes' if any(x['unsubscribe_header_present']=='yes' for x in items) else 'no',
                        'proposed_action':suggested,'actual_action':'SEE MESSAGE REPORT / AUDIT'})
    atomic_csv(REPORT_DIR / f'{account}_messages.csv',FIELDS,message_rows)
    atomic_csv(REPORT_DIR / f'{account}_senders.csv',
               ['sender','messages','categories','contains_important_or_transactional',
                'unsubscribe_header_present','proposed_action','actual_action'],senders)
    return counts,len(senders)


def gmail_messages(config,days,limit, required_scope=GMAIL_READ_SCOPE, backlog=False):
    token = BASE / 'gmail_token.json'
    if not token.exists():
        LOG.info('Gmail SKIPPED: gmail_token.json missing')
        return []
    creds=Credentials.from_authorized_user_file(str(token))
    if required_scope not in set(creds.scopes or []) and not (required_scope == GMAIL_READ_SCOPE and GMAIL_MODIFY_SCOPE in set(creds.scopes or [])):
        raise RuntimeError('Gmail token lacks required scope: ' + required_scope.split('/')[-1] + '. Reauthorise on Windows; no mailbox changes made')
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            token.write_text(creds.to_json()) # OAuth token cache update only; not a mailbox change
            token.chmod(0o600)
        else:
            raise RuntimeError('Gmail authorisation expired or invalid')
    session=requests.Session()
    session.headers['Authorization']='Bearer '+creds.token
    def get(url,params=None):
        r=session.get(url,params=params,timeout=30)
        r.raise_for_status()
        return r.json()
    identity=get('https://gmail.googleapis.com/gmail/v1/users/me/profile')['emailAddress'].lower()
    if identity != config['gmail_email'].lower():
        raise RuntimeError('Gmail account mismatch')
    url='https://gmail.googleapis.com/gmail/v1/users/me/messages'
    # Gmail lists newest first. Rotate through pages between scans, so older
    # mail is eventually inspected even if the first page contains no moves.
    page_size=min(500,limit)
    page_index=max(0,int(read_json(BASE / 'backlog_progress.json',{}).get('gmail',0))) if backlog else 0
    page_token=None
    response=None
    for index in range(page_index+1):
        params={'q':f'in:inbox newer_than:{days}d','maxResults':page_size}
        if page_token:
            params['pageToken']=page_token
        response=get(url,params)
        next_token=response.get('nextPageToken')
        if index < page_index:
            if not next_token:
                # Inbox got shorter since the previous scan. Wrap to page zero.
                page_index=0
                response=get(url,{'q':f'in:inbox newer_than:{days}d','maxResults':page_size})
                break
            page_token=next_token
    ids=response.get('messages',[])
    next_page=page_index+1 if backlog and response.get('nextPageToken') else 0
    records=[]
    wanted=['From','Subject','List-Unsubscribe','List-ID','Precedence','Auto-Submitted','Authentication-Results','List-Unsubscribe-Post']
    for entry in ids:
        datum=get(f"{url}/{entry['id']}",{'format':'metadata','metadataHeaders':wanted})
        hs={x['name'].lower():x['value'] for x in datum['payload'].get('headers',[])}
        fl=flags(hs)
        if fl['auto']:
            continue
        records.append({'id':entry['id'],'sender':decoded(hs.get('from')),
                        'subject':decoded(hs.get('subject')),'flags':fl,
                        'gmail_labels':datum.get('labelIds',[]),
                        'age_days': max(0,(time.time()*1000-int(datum.get('internalDate',0)))/86400000) if datum.get('internalDate') else None})
    if backlog:
        save_backlog_progress('gmail',next_page)
        LOG.info('Gmail backlog: scanned page=%d, next_page=%d, batch_size=%d',page_index,next_page,len(ids))
    return records


def yahoo_messages(config,days,limit,backlog=False):
    token=BASE / 'yahoo_app_password.txt'
    if not token.exists():
        LOG.info('Yahoo SKIPPED: password file missing')
        return []
    pw=token.read_text().strip()
    if not pw:
        raise RuntimeError('Empty Yahoo app password')
    records=[]
    with imaplib.IMAP4_SSL('imap.mail.yahoo.com',993,ssl_context=ssl.create_default_context(),timeout=30) as imap:
        imap.login(config['yahoo_email'],pw)
        typ,_=imap.select('INBOX',readonly=True)
        if typ!='OK':
            raise RuntimeError('Yahoo inbox select failed')
        since=(datetime.now(timezone.utc)-timedelta(days=days)).strftime('%d-%b-%Y')
        typ,data=imap.uid('SEARCH',None,'SINCE',since)
        if typ!='OK':
            raise RuntimeError('Yahoo UID search failed')
        # Yahoo UID SEARCH returns matching UIDs; rotate over slices of that
        # result. Avoid an unbounded FETCH of the entire Inbox each scan.
        all_uids=data[0].split()[::-1] if data and data[0] else []
        page_index=max(0,int(read_json(BASE / 'backlog_progress.json',{}).get('yahoo',0))) if backlog else 0
        start=page_index*limit
        if start>=len(all_uids):
            page_index=0
            start=0
        batch=all_uids[start:start+limit]
        next_page=page_index+1 if backlog and start+limit<len(all_uids) else 0
        for uid in batch:
            typ,raw=imap.uid('FETCH',uid,'(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT LIST-UNSUBSCRIBE LIST-ID PRECEDENCE AUTO-SUBMITTED AUTHENTICATION-RESULTS)])')
            if typ!='OK':
                continue
            payload=next((p[1] for p in raw if isinstance(p,tuple)),b'')
            hs=email.message_from_bytes(payload)
            header_map={x.lower():str(hs.get(x,'')) for x in ['From','Subject','List-Unsubscribe','List-ID','Precedence','Auto-Submitted','Authentication-Results','List-Unsubscribe-Post']}
            fl=flags(header_map)
            if fl['auto']:
                continue
            records.append({'id':uid.decode('ascii','replace'),'sender':decoded(hs.get('From')),
                            'subject':decoded(hs.get('Subject')),'flags':fl, 'age_days': None})
    if backlog:
        save_backlog_progress('yahoo',next_page)
        LOG.info('Yahoo backlog: scanned page=%d, next_page=%d, batch_size=%d',page_index,next_page,len(batch))
    return records


def current_options():
    """The running app organises mail; Home Assistant Start/Stop is the control.

    Only reversible folder/label changes are permitted. Never delete or
    unsubscribe, regardless of residual legacy options.
    """
    return {
        'enable_actions':True,
        'auto_file_marketing':True,
        'bulk_backlog_enabled':True,
        'expanded_actions_enabled':False,
        'trash_old_delivery_updates':False,
        'enable_unsubscribe':False,
        'max_actions_per_run':50,
        'interval_minutes':15,
        'history_days':3650,
        'max_per_account':500,
    }, 'safe'


def operation_description(op):
    return 'NO ACTION' if op is None else ('MOVE TO ' + op[1] if op[0]=='move' else op[0].upper())


def expanded_policy():
    data=read_json(EXPANDED_POLICY,{'senders':{}})
    if not isinstance(data,dict) or not isinstance(data.get('senders',{}),dict):
        raise ValueError('Invalid expanded_senders.json')
    return validate_sender_policy(data.get('senders',{}))


def validate_sender_policy(senders):
    result={}
    for key,val in senders.items():
        sender=str(key).lower().strip()
        if sender.count('@')!=1 or '*' in sender or not isinstance(val,list):
            raise ValueError('Invalid sender policy')
        if any(v not in ('Purchases','Delivery Updates','Low Priority','Promotions','Newsletters') for v in val):
            raise ValueError('Invalid sender policy category')
        result[sender]=set(val)
    return result


def approved_policy():
    path=BASE / 'approved_senders.json'
    data=read_json(path,{})
    if not isinstance(data,dict) or not isinstance(data.get('senders',{}),dict):
        raise ValueError('Invalid approved_senders.json')
    return validate_sender_policy(data.get('senders',{}))


def strong_marketing_evidence(msg):
    """Clearly commercial subject without relying on untrusted list headers.

    This only grants a reversible promotional move when explicitly opted into
    bulk backlog filing; no unsubscribe, deletion or spam handling.
    """
    subject=normalised_subject(msg.get('subject',''))
    # Narrow phrases that indicate advertising, not account or order activity.
    phrases=(
        '% off', 'flash sale', 'clearance sale', 'sale ends',
        'discount code', 'promo code', 'shop now', 'sitewide sale',
        'exclusive deal', 'black friday sale', 'limited time offer',
        'half price', 'special offer', 'new arrivals', 'free shipping',
    )
    raw=msg.get('subject','')
    return (
        any(p in subject for p in phrases)
        and not any(unicodedata.category(ch)=='Cf' for ch in raw)
        and not any(x in subject for x in (
            'invoice','refund','payment','order','security','verify',
            'password','delivery','shipment','tracking','account',
            'bank','statement','insurance','cancelled','canceled',
        ))
    )


def proposed_operation(msg,category,mode,policy,opts):
    """Never act on unknown or mixed/unverified policy senders. Only explicit opt-in."""
    sender=address(msg['sender'])
    if mode=='dry_run' or not opts.get('enable_actions',False):
        return None
    explicitly_approved = category in policy.get(sender,set())
    # Optional bulk filing is reversible. Only marketing/list mail qualifies;
    # security, purchases, transactions, suspicious and mixed senders stay put.
    auto_marketing = (
        opts.get('auto_file_marketing',False)
        and category in ('Promotions','Newsletters')
        and bool(sender) and '@' in sender
        and (
            bool(msg.get('flags',{}).get('list'))
            or (category=='Promotions'
                and 'CATEGORY_PROMOTIONS' in msg.get('gmail_labels',[])
                and 'STARRED' not in msg.get('gmail_labels',[]))
            or (opts.get('bulk_backlog_enabled',False)
                and category=='Promotions'
                and (strong_marketing_evidence(msg) or commercial_sender_subject(msg)))
        )
        and (opts.get('bulk_backlog_enabled',False)
             or sender not in opts.get('_protected_marketing_senders',set()))
        # Obfuscated Unicode format characters in subjects are not safe to
        # bulk-file on a subject/list-header heuristic.
        and not any(unicodedata.category(ch)=='Cf' for ch in msg.get('subject',''))
    )
    if not explicitly_approved and not auto_marketing:
        return None
    # Protect time-sensitive/account/financial exceptions regardless of sender preferences.
    if category in ('Important','Transactions','Purchase Issues','Suspicious','Review'):
        return None
    if category=='Purchases':
        return ('move','EO Purchases')
    if category=='Delivery Updates':
        if mode=='balanced' and opts.get('trash_old_delivery_updates',False) and msg.get('age_days') is not None and msg['age_days'] >= 7:
            return ('trash',None)
        return ('move','EO Low Priority')
    if category=='Low Priority':
        return ('move','EO Low Priority')
    if category in ('Promotions','Newsletters'):
        return ('move','EO Promotions' if category=='Promotions' else 'EO Review')
    return None


def audit(account,msg,category,op,outcome):
    BASE.mkdir(parents=True,exist_ok=True)
    record={'utc':datetime.now(timezone.utc).isoformat(),'account':account,
            'message_hash':hashlib.sha256((account+':'+str(msg['id'])).encode()).hexdigest()[:20],
            'category':category,'operation':op[0] if op else 'none',
            'destination':op[1] if op else None,'outcome':outcome}
    with AUDIT_FILE.open('a',encoding='utf-8') as f:
        f.write(json.dumps(record,ensure_ascii=False)+'\n')
    AUDIT_FILE.chmod(0o600)


def unsubscribe_policy():
    # Each sender and endpoint hostname must be explicitly approved by the user.
    data=read_json(BASE/'approved_unsubscribe.json',{'senders':{}})
    senders=data.get('senders',{})
    if not isinstance(senders,dict):
        raise ValueError('Invalid unsubscribe sender policy')
    result={}
    for sender,host in senders.items():
        key=str(sender).lower().strip(); hostname=str(host).lower().strip()
        if key.count('@')!=1 or '*' in key or not re.fullmatch(r'[a-z0-9.-]+',hostname) or '.' not in hostname:
            raise ValueError('Unsubscribe policy requires exact sender and hostname')
        result[key]=hostname
    return result


def gmail_oneclick_unsubscribe_candidates(messages,opts,mode):
    if mode!='balanced' or not opts.get('enable_actions',False) or not opts.get('enable_unsubscribe',False):
        return []
    policy=unsubscribe_policy()
    already=read_json(BASE/'completed_unsubscribe.json',{})
    if not isinstance(already,dict):
        raise ValueError('Invalid completed unsubscribe registry')
    result=[]; seen=set()
    for msg in messages:
        sender=address(msg['sender'])
        if sender in already or sender in seen or sender not in policy:
            continue
        cat,_,_,_=classify(msg,preferences())
        if cat not in ('Promotions','Newsletters') or not msg['flags'].get('oneclick',False):
            continue
        # Select only the exact approved HTTPS hostname, not arbitrary sender links.
        candidate_urls=msg['flags'].get('unsubscribe_urls',[])
        selected=None
        for url in candidate_urls:
            parsed=urlparse(html.unescape(url))
            if parsed.scheme=='https' and parsed.hostname and parsed.hostname.lower()==policy[sender] and not parsed.username and not parsed.password and parsed.port in (None,443) and not parsed.fragment:
                selected=url
                break
        if selected:
            result.append((msg,cat,selected));seen.add(sender)
    return result


def execute_oneclick(msg,category,url):
    # RFC 8058 one-click; never follow redirects or download remote content.
    response=requests.post(url,data='List-Unsubscribe=One-Click',
                           headers={'Content-Type':'application/x-www-form-urlencoded'},
                           timeout=10,allow_redirects=False)
    if response.status_code not in (200,202,204):
        return 'failed_http_'+str(response.status_code)
    sender=address(msg['sender'])
    registry=read_json(BASE/'completed_unsubscribe.json',{})
    registry[sender]={'utc':datetime.now(timezone.utc).isoformat()}
    tmp=BASE/'completed_unsubscribe.json.tmp'
    tmp.write_text(json.dumps(registry,indent=2)+'\n',encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(BASE/'completed_unsubscribe.json')
    return 'unsubscribe_requested'


def gmail_writer(config):
    token=BASE/'gmail_token.json'
    creds=Credentials.from_authorized_user_file(str(token))
    if GMAIL_MODIFY_SCOPE not in set(creds.scopes or []):
        raise RuntimeError('Gmail write blocked: gmail.modify scope missing')
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            token.write_text(creds.to_json())
            token.chmod(0o600)
        else:
            raise RuntimeError('Gmail token expired')
    session=requests.Session()
    session.headers['Authorization']='Bearer '+creds.token
    api='https://gmail.googleapis.com/gmail/v1/users/me'
    def call(method,path,**kwargs):
        r=session.request(method,api+path,timeout=30,**kwargs)
        r.raise_for_status()
        return r.json() if r.content else {}
    identity=call('GET','/profile')['emailAddress'].lower()
    if identity!=config['gmail_email'].lower():
        raise RuntimeError('Gmail account mismatch; write blocked')
    labels={x['name']:x['id'] for x in call('GET','/labels').get('labels',[])}
    def apply(msg,op):
        kind,dest=op
        # Recheck current message still in Inbox and exclude spam, trash and starred.
        state=call('GET','/messages/'+msg['id'],params={'format':'minimal'})
        ids=set(state.get('labelIds',[]))
        if 'INBOX' not in ids or 'SPAM' in ids or 'TRASH' in ids or 'STARRED' in ids:
            return 'skipped_not_in_inbox_or_protected'
        if kind=='trash':
            call('POST','/messages/'+msg['id']+'/trash')
            return 'trashed'
        if dest not in labels:
            created=call('POST','/labels',json={'name':dest,'labelListVisibility':'labelShow','messageListVisibility':'show','type':'user'})
            labels[dest]=created['id']
        call('POST','/messages/'+msg['id']+'/modify',json={'addLabelIds':[labels[dest]],'removeLabelIds':['INBOX']})
        return 'moved'
    return apply


def yahoo_apply_batch(config, items):
    """MOVE approved Yahoo messages; fail closed and name the failing stage.

    No copy/delete fallback. Do not retry a MOVE with an uncertain outcome.
    """
    outcomes=[]
    pw=(BASE/'yahoo_app_password.txt').read_text().strip()
    stage='connect'
    try:
        with imaplib.IMAP4_SSL('imap.mail.yahoo.com',993,ssl_context=ssl.create_default_context(),timeout=30) as client:
            stage='login'
            client.login(config['yahoo_email'],pw)
            stage='capabilities'
            caps={part.upper() for cap in client.capabilities for part in (cap.decode('ascii','replace') if isinstance(cap,bytes) else str(cap)).split()}
            if 'MOVE' not in caps:
                raise RuntimeError('Yahoo does not advertise IMAP MOVE')
            stage='select_inbox'
            typ,_=client.select('INBOX',readonly=False)
            if typ!='OK':
                raise RuntimeError('Could not select Inbox for writing')
            stage='search_uids'
            typ,data=client.uid('SEARCH',None,'ALL')
            if typ!='OK':
                raise RuntimeError('Could not search Inbox UIDs')
            available=set(data[0].split())
            # Destination folders are checked once, not CREATEd every message.
            folders=set()
            for msg,category,op in items:
                uid=str(msg['id']).encode('ascii')
                if uid not in available:
                    outcomes.append((msg,category,op,'skipped_not_in_inbox'))
                    continue
                stage='fetch_flags'
                typ,raw=client.uid('FETCH',uid,'(FLAGS)')
                if typ!='OK':
                    outcomes.append((msg,category,op,'failed_flags'))
                    continue
                flagdata=b' '.join(x[0] if isinstance(x,tuple) else x for x in raw if isinstance(x,(bytes,tuple)))
                if b'\\Flagged' in flagdata or b'\\Deleted' in flagdata:
                    outcomes.append((msg,category,op,'skipped_flagged'))
                    continue
                target='Trash' if op[0]=='trash' else op[1]
                if target not in folders and target!='Trash':
                    # Yahoo accepts quoted SELECT and UID MOVE; LIST with a
                    # mailbox pattern failed in production, so do not use it.
                    quoted='"'+target.replace('"','')+'"'
                    stage='select_destination'
                    typ_dest,_=client.select(quoted,readonly=True)
                    if typ_dest!='OK':
                        stage='create_destination'
                        typ_created,_=client.create(quoted)
                        if typ_created!='OK':
                            raise RuntimeError('Yahoo destination could not be created')
                        stage='verify_destination'
                        typ_dest,_=client.select(quoted,readonly=True)
                        if typ_dest!='OK':
                            raise RuntimeError('Yahoo destination not selectable')
                    stage='reselect_inbox'
                    typ_inbox,_=client.select('INBOX',readonly=False)
                    if typ_inbox!='OK':
                        raise RuntimeError('Yahoo Inbox could not be reselected for writing')
                    # Revalidate the UID after switching mailboxes before MOVE.
                    stage='recheck_inbox_uid'
                    typ_search,data_search=client.uid('SEARCH',None,'ALL')
                    if typ_search!='OK':
                        raise RuntimeError('Yahoo Inbox UID recheck failed')
                    available=set(data_search[0].split()) if data_search and data_search[0] else set()
                    if uid not in available:
                        outcomes.append((msg,category,op,'skipped_not_in_inbox'))
                        continue
                    stage='recheck_flags'
                    typ_flags,raw_flags=client.uid('FETCH',uid,'(FLAGS)')
                    if typ_flags!='OK':
                        outcomes.append((msg,category,op,'failed_flags'))
                        continue
                    fresh_flags=b' '.join(x[0] if isinstance(x,tuple) else x for x in raw_flags if isinstance(x,(bytes,tuple)))
                    if b'\\Flagged' in fresh_flags or b'\\Deleted' in fresh_flags:
                        outcomes.append((msg,category,op,'skipped_flagged'))
                        continue
                    folders.add(target)
                stage='uid_move'
                typ,detail=client.uid('MOVE',uid,'"'+target+'"')
                if typ!='OK':
                    # Do not attempt a second MOVE: outcome is uncertain.
                    raise RuntimeError('UID MOVE rejected by server')
                outcomes.append((msg,category,op,'moved'))
                stage='done'
    except (imaplib.IMAP4.error, OSError, RuntimeError, ValueError) as exc:
        # No credentials, message contents, or server-returned strings in logs.
        LOG.error('Yahoo move failed at stage=%s, error_type=%s; stopping this batch; verify messages before retry',stage,type(exc).__name__)
        # Earlier moves may already have succeeded, but the caller must not
        # misreport the whole batch as if nothing changed.
        for msg,category,op,result in outcomes:
            audit('yahoo',msg,category,op,result)
            LOG.info('Yahoo approved action %s: %s',op[0],result)
        raise
    return outcomes


def full_policy(base, expanded):
    merged={key:set(vals) for key,vals in base.items()}
    for key,vals in expanded.items():
        merged.setdefault(key,set()).update(vals)
    return merged


def once():
    config=read_json(BASE/'accounts.json')
    opts,mode=current_options()
    bulk=bool(opts.get('bulk_backlog_enabled',False))
    days=min(3650,max(1,int(opts.get('history_days',30))))
    limit=min(500,max(1,int(opts.get('max_per_account',150))))
    prefs=preferences()
    policy=approved_policy()
    more=expanded_policy()
    preview_policy=full_policy(policy,more)
    if opts.get('expanded_actions_enabled',False):
        policy=preview_policy
    total=Counter()
    for account,fetch in (('gmail',gmail_messages),('yahoo',yahoo_messages)):
        if not config.get(f'{account}_email'):
            LOG.info('%s SKIPPED: no configured address',account.capitalize())
            continue
        try:
            # Reads work with either OAuth scope; writes only with gmail.modify.
            messages=fetch(config,days,limit,backlog=bulk) if account=='yahoo' else fetch(config,days,limit,GMAIL_READ_SCOPE if mode=='dry_run' or not opts.get('enable_actions',False) else GMAIL_MODIFY_SCOPE,backlog=bulk)
            # A sender with any non-marketing mail in this scan is excluded
            # from bulk marketing filing, even for its promotional messages.
            # Explicit sender approvals retain their existing behaviour.
            protective_categories={'Important','Transactions','Purchases','Purchase Issues','Delivery Updates','Suspicious','Review','Low Priority'}
            opts['_protected_marketing_senders']={
                address(msg['sender']) for msg in messages
                if classify(msg,prefs)[0] in protective_categories
            }
            counts,senders=write_reports(account,messages,prefs,preview_policy)
            total.update(counts)
            candidate_count=sum(proposed_operation(msg,classify(msg,prefs)[0],'safe',preview_policy,{'enable_actions':True,'trash_old_delivery_updates':False}) is not None for msg in messages)
            LOG.info('%s: reversible move candidates (preview)=%d; expanded actions enabled=%s',account.capitalize(),candidate_count,bool(opts.get('expanded_actions_enabled',False)))
            LOG.info('%s: analysed %d messages from %d senders; categories=%s',account.capitalize(),len(messages),senders,dict(counts))
            work=[]
            for msg in messages:
                cat,*_=classify(msg,prefs)
                op=proposed_operation(msg,cat,mode,policy,opts)
                if op:
                    work.append((msg,cat,op))
            # Explain why promotional mail did not qualify, without logging senders
            # or message subjects. Counts are from this page, not the whole Inbox.
            marketing=[msg for msg in messages if classify(msg,prefs)[0] in ('Promotions','Newsletters')]
            missing_list=sum(not msg.get('flags',{}).get('list') for msg in marketing)
            subject_only=sum(
                not msg.get('flags',{}).get('list')
                and classify(msg,prefs)[0]=='Promotions'
                and strong_marketing_evidence(msg)
                for msg in marketing
            )
            LOG.info('%s marketing eligibility: classified=%d, missing_list_header=%d, strong_subject_only=%d, actual_eligible=%d',
                     account.capitalize(),len(marketing),missing_list,subject_only,len(work))
            LOG.info('%s: eligible for approved actions=%d, mode=%s, enabled=%s, auto_marketing=%s',account.capitalize(),len(work),mode,bool(opts.get('enable_actions',False)),bool(opts.get('auto_file_marketing',False)))
            if account=='gmail':
                for msg,cat,url in gmail_oneclick_unsubscribe_candidates(messages,opts,mode)[:3]:
                    try:
                        outcome=execute_oneclick(msg,cat,url)
                    except Exception as exc:
                        outcome='failed_'+type(exc).__name__
                    audit('gmail',msg,cat,('unsubscribe',None),outcome)
                    LOG.info('Gmail approved one-click unsubscribe: %s',outcome)

            max_actions=max(0,min(50,int(opts.get('max_actions_per_run',10))))
            if len(work)>max_actions:
                LOG.info('%s: limiting actions to %d per run',account.capitalize(),max_actions)
                work=work[:max_actions]
            outcomes={}
            if work:
                if account=='gmail':
                    writer=gmail_writer(config)
                    for msg,cat,op in work:
                        try:
                            result=writer(msg,op)
                        except Exception as exc:
                            result='failed_'+type(exc).__name__
                        audit(account,msg,cat,op,result)
                        outcomes[str(msg['id'])]=result
                        LOG.info('Gmail approved action %s: %s',op[0],result)
                else:
                    for msg,cat,op,result in yahoo_apply_batch(config,work):
                        audit(account,msg,cat,op,result)
                        outcomes[str(msg['id'])]=result
                        LOG.info('Yahoo approved action %s: %s',op[0],result)
            if outcomes:
                write_reports(account,messages,prefs,preview_policy,outcomes)
        except Exception as ex:
            LOG.error('%s: scan/actions failed safely (%s); consult configuration and OAuth scopes',account.capitalize(),type(ex).__name__)
    LOG.info('Outlook disabled. Mode=%s. Total classifications=%s',mode,dict(total))


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    LOG.info('Email Organiser v1.4.0; one-switch reversible bulk filing')
    while True:
        try:
            once()
        except Exception as ex:
            LOG.error('Scan failed safely (%s)',type(ex).__name__)
        time.sleep(15*60)


if __name__=='__main__':
    main()

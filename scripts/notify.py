"""Family to-do notifications — run on GitHub Actions, started every ~5 minutes from outside (workflow_dispatch).
Only three kinds of alerts, all for the kids (FAMILY["kids"]):
 1. A parent (admin) added a task to a kid's own list -> that kid gets it (several in one run -> one message)
 2. Activity (activities board) starts soon            -> the kid gets a reminder ~30 minutes before
 3. Exam tomorrow (exam board)                          -> the kid gets a reminder the evening before (from 17:00)
Also: tasks in the recycle bin for more than 14 days are removed for good (no alert).
Needs two secrets:
  FIREBASE_SA — the Firebase service-account JSON
  FAMILY      — the family settings JSON (names, kids, admins, app address); see README.md
Optional FAMILY keys: "chug_before_min" (default 30), "exam_eve" (default "17:00"), "timezone" (default Asia/Jerusalem).
This repository is public, so the logs never print names, task titles or ids — only counts."""
import os, json, time, datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore, messaging

FAM = json.loads(os.environ['FAMILY'])
APP_URL = FAM['app_url'].rstrip('/') + '/'
ICON = APP_URL + 'icon-192.png'
TZ = ZoneInfo(FAM.get('timezone', 'Asia/Jerusalem'))
NAMES = FAM['names']                                   # {person id: name}
FEMALE = set(FAM.get('female', []))
ADMINS = set(FAM.get('admins', []))                    # parents
KIDS = [k for k in FAM.get('kids', []) if k in NAMES]  # who gets alerts
CHUG_BEFORE = int(FAM.get('chug_before_min', 30))
EXAM_EVE = tuple(int(x) for x in FAM.get('exam_eve', '17:00').split(':'))
SPECIAL = ('exam', 'chug', 'bday', 'shop', 'shopList', 'event')   # board items, not plain tasks
SENT = {'new': 0, 'chug': 0, 'exam': 0, 'failed': 0, 'bin': 0}

firebase_admin.initialize_app(credentials.Certificate(json.loads(os.environ['FIREBASE_SA'])))
db = firestore.client()


def push(person, title, body):
    docs = list(db.collection('tokens').where('person', '==', person).stream())
    if not docs:
        return 0
    msgs = [messaging.Message(
        token=d.id,
        webpush=messaging.WebpushConfig(
            notification=messaging.WebpushNotification(title=title, body=body, icon=ICON, badge=ICON, language='he', direction='rtl'),
            fcm_options=messaging.WebpushFCMOptions(link=APP_URL)))
        for d in docs]
    res = messaging.send_each(msgs)
    for d, r in zip(docs, res.responses):
        if not r.success:
            SENT['failed'] += 1
            code = str(getattr(r.exception, 'code', '') or '').upper()
            if 'UNREGISTERED' in code or 'NOT_FOUND' in code or 'INVALID_ARGUMENT' in code:
                d.reference.delete()
    return res.success_count


def short(tasks, n=4):
    lines = ['• ' + t['title'] for t in tasks[:n]]
    if len(tasks) > n:
        lines.append(f'ועוד {len(tasks) - n}…')
    return '\n'.join(lines)


meta_ref = db.document('meta/notify')
state = meta_ref.get().to_dict() or {}
now_ms = int(time.time() * 1000)
now = datetime.datetime.now(TZ)
today = now.strftime('%Y-%m-%d')
tomorrow = (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
now_min = now.hour * 60 + now.minute

# ---------- 1. a parent added a task to a kid's list ----------
last = state.get('lastNew') or now_ms          # first run: start from now (no flood of old tasks)
per_kid = {}
for d in db.collection('tasks').where('createdAt', '>', last).stream():
    t = d.to_dict()
    if t.get('archived') or t.get('done') or t.get('deleted') or any(t.get(k) for k in SPECIAL):
        continue
    if t.get('createdBy') in ADMINS and t.get('list') in KIDS and t.get('list') != t.get('createdBy'):
        per_kid.setdefault(t['list'], []).append(t)
for kid, ts in per_kid.items():
    ts.sort(key=lambda x: x.get('createdAt', 0))
    if len(ts) == 1:
        by = ts[0].get('createdBy')
        title = f'🆕 {NAMES.get(by, "")} {"הוסיפה" if by in FEMALE else "הוסיף"} לך משימה'
        body = ts[0]['title'] + (f' · {ts[0]["time"]}' if ts[0].get('due') == today and ts[0].get('time') else '')
    else:
        title = f'🆕 {len(ts)} משימות חדשות בשבילך'
        body = short(ts)
    SENT['new'] += push(kid, title, body)
state['lastNew'] = now_ms

# ---------- 2. activity starts soon ----------
done_chug = state.get('chugSent') if state.get('chugDay') == today else []
done_chug = list(done_chug or [])
for d in db.collection('tasks').where('chug', '==', True).stream():
    c = d.to_dict()
    kid = c.get('who')
    if kid not in KIDS:
        continue
    if c.get('mode') == 'var':
        times = [x.get('t') for x in c.get('dates') or [] if x.get('date') == today]
    else:
        wd = (now.weekday() + 1) % 7                 # app: 0 = Sunday
        times = [s.get('t') for s in c.get('slots') or [] if int(s.get('d', -1)) == wd]
    for tm in times:
        try:
            start = int(tm[:2]) * 60 + int(tm[3:5])
        except (TypeError, ValueError):
            continue
        key = d.id + '|' + tm
        if key in done_chug or not (0 < start - now_min <= CHUG_BEFORE):
            continue
        SENT['chug'] += push(kid, f'🏅 {c.get("title", "חוג")} ב־{tm}', f'עוד {start - now_min} דקות · לא לשכוח להתארגן 🎒')
        done_chug.append(key)
state['chugDay'] = today
state['chugSent'] = done_chug

# ---------- 3. exam tomorrow (the evening before) ----------
if (now.hour, now.minute) >= EXAM_EVE and now.hour < 22 and state.get('examEve') != today:
    per_kid = {}
    for d in db.collection('tasks').where('exam', '==', True).stream():
        t = d.to_dict()
        if t.get('due') == tomorrow and t.get('kid') in KIDS and not t.get('done') and not t.get('deleted'):
            per_kid.setdefault(t['kid'], []).append(t)
    for kid, ts in per_kid.items():
        SENT['exam'] += push(kid, '📝 מחר מבחן!', '\n'.join('• ' + (t.get('subject') or t.get('title', '')) for t in ts) + '\nבהצלחה 🍀')
    state['examEve'] = today

# ---------- recycle bin: tasks deleted more than 14 days ago are removed for good ----------
cutoff = now_ms - 14 * 86400000
for d in db.collection('tasks').where('deleted', '==', True).stream():
    if (d.to_dict().get('deletedAt') or 0) < cutoff:
        d.reference.delete(); SENT['bin'] += 1

meta_ref.set(state)
print('done', now.strftime('%Y-%m-%d %H:%M'), ' '.join(f'{k}={v}' for k, v in SENT.items() if v))

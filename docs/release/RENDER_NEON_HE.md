# הדגמת מוזמנים ב־Render + Neon — 22.10.2026

זהו מדריך הפריסה של עץ השחרור `storage/submission-release`, בענף
`release/submission-2026-10-22`. פרסום הקוד לענף GitHub אושר בנפרד;
המשתמש דיווח על אתר חי ב־https://viral-lab-judges-demo.onrender.com ואישר
הפעלת חילוץ Gemini למסמכים סינתטיים שמעלה משתמש מחובר, לאחר הסכמה מפורשת.
בתיקון זה לא בוצעו deploy ידני, קריאת Gemini או גישה ל־Neon או למסד המקומי.

## Retry מוגבל ל־Gemini HTTP 503 UNAVAILABLE בלבד

אבחון חי נוסף אישר `PROVIDER_UNAVAILABLE`, ‏`stage=provider_request`,
`failure_kind=provider_http`, HTTP פנימי 503, ‏`provider_status=UNAVAILABLE`
ו־`elapsed_ms=2504`. זהו כשל ספק מהיר, ולא שגיאת התור, Neon או Render.
בענן בלבד המתאם מאפשר עד שני retries לקריאת `generate_content` עם התאמה
מדויקת ל־HTTP 503 וגם UNAVAILABLE. שלושה ניסיונות לכל היותר; המתנות
אקספוננציאליות סביב 3 ו־8 שניות עם jitter של ±20% (2.4–3.6 ואז 6.4–9.6).
אין retry ל־504, 429, authentication, כשל רשת, parsing/schema, DB או 503
ללא UNAVAILABLE. לא מפעילים מחדש upload, workflow או persistence.
רשומת processing נשמרת פעם אחת לפני הספק, ו־completion פעם אחת לאחר התוצאה
הסופית. אין retry לאחר הצלחה או בעקבות כשל שמירה. לאחר מיצוי הניסיונות
חוזר הכשל האמיתי והתור הסדרתי הקיים ממשיך לקובץ הבא.

timeout נשאר 120 שניות לכל ניסיון כברירת מחדל, המודל/ה־prompt/הסכמה והגנות
הגישה לא השתנו. שלושה ניסיונות ארוכים עשויים להימשך מעל שש דקות, בלי
הבטחת הצלחה; זה אינו timeout כולל של הבקשה. אין retry פנימי של SDK:
`HttpRetryOptions(attempts=1)` נשאר. בדיקת SDK מותקן עם HTTP transport
מדומה וחסימת sockets מאמתת שאין הכפלת ניסיונות ושה־read timeout הוא 120 שניות.
`provider_attempt_count` נוסף לאבחון וללוג הסופי; `cloud_provider_attempt`
מציין כל ניסיון. שגיאת הספק האחרונה נשמרת לאחר מיצוי או שינוי סוג הכשל,
ללא גוף תגובה, מסמך או סודות. ב־startup צפויים `adapter_retries=2`,
`retry_only_http_503_unavailable=true`, ‏`sdk_attempts=1`, ‏`sdk_timeout_ms=120000`.
אין צורך בשינוי משתני Render, סודות, Blueprint או Neon; נדרשת פריסה ידנית
של ה־commit החדש לאחר פרסומו. הודעת ה־commit כוללת `[skip render]`.

## התקדמות אוטומטית של אצוות קבצים — 22.10.2026

לאחר פריסת 1fe604e המשתמש דיווח על חילוץ PDF סינתטי מוצלח ועל ארבעה
כשלי HTTP 503 באצווה. נמצא באג Frontend: כשל שמור בענן נזרק כ־APIError
וה־catch הפעיל `break`, בעוד שבדיקת התור הישנה החזירה כשל בגוף HTTP 200.
התיקון ממשיך לקובץ הבא גם בכשל HTTP או בתוצאת רשת לא ידועה. אין שליחה
חוזרת של אותו פריט, concurrency נשאר 1, ונעילת ref מונעת לולאות כפולות
מלחיצות מהירות. מוצגים סטטוס וקוד בטוח לכל קובץ, HTTP כשזמין, התקדמות
וסיכום הצלחות/כישלונות. תוצאה לא ידועה אינה הוכחה שהשרת לא שמר רשומה;
לפני העלאה ידנית חוזרת יש לבדוק ברשומות השמורות. התור אינו עמיד לסגירת דפדפן.
גם כשל 429 אינו מפעיל retry; לכל קובץ נשלחת בקשת upload אחת בלבד, בלי
לעקוף מגבלות שרת. חסימת התחברות/CSRF ‏401/403 ללא תוצאת חילוץ שמורה עוצרת
את התור ומחייבת טיפול בגישה. הרשאות, הסכמה, timeout, מודל ו־Backend לא שונו.

503 בלבד אינו מוכיח כשל Render או rate limit: `PROVIDER_UNAVAILABLE` עשוי
להצביע על ספק, `CLOUD_DATABASE_NOT_READY`/`CLOUD_DATABASE_CREATE_FAILED`/
`CLOUD_DATABASE_COMPLETE_FAILED` על מסד, ו־`CLOUD_BACKEND_UNEXPECTED` על Backend.
`failure_status` גם ממפה קודים אחרים שאינם ברשימה מפורשת ל־503. Rate limit
מזוהה הוא 429 ו־timeout מזוהה הוא 504. כדי לאבחן את ארבעת הכשלים החיים
נדרשים `error.code`, ‏`failure.code` אם קיים, `diagnostic` ו־`X-Request-ID` בלבד;
אין להעתיק תגובה מלאה, cookies, review_token, מסמך או סודות.

## אבחון 503/504 — פרסום מקור אושר; פריסה דורשת פעולה נפרדת

בעקבות תיקון האבחון המשתמש דיווח מהאתר החי על `PROVIDER_TIMEOUT`, עם
`stage=provider_request`, `failure_kind=provider_http`, HTTP פנימי 504,
`provider_status=DEADLINE_EXCEEDED`, `timeout_phase=provider_deadline`
ו־`elapsed_ms=44289`. זה מאמת deadline מהספק, לא כשל readiness או timeout
מקומי של הלקוח. עדיין אין הוכחה שהגדלת timeout הלקוח תפתור deadline פנימי בספק.

המסלול: שער התחברות/CSRF → schema readiness → בדיקת קובץ →
`run_in_threadpool(CloudWorkflow.create)` → transaction של יצירת מסמך →
Gemini policy → יצירת SDK client → `generate_content` → JSON/canonical →
transaction של completion → תגובת HTTP. חיבור PostgreSQL אינו מוחזק לאורך
קריאת הספק. אין durable job או התאוששות אוטומטית לרשומת processing לאחר restart.
העיבוד סינכרוני ביחס לבקשת ההעלאה, אך אינו רץ על event loop. בדיקה באותה
לולאה מאמתת ש־health ממשיך לענות ושהעלאה נוספת מקבלת 429 בזמן ספק ממתין.

לבדיקה מבוקרת מגבלת ה־SDK בענן הוגדלה מ־45,000 ל־120,000 מילישניות.
`GEMINI_TIMEOUT_SECONDS` הוא משתנה Backend אופציונלי: מספר שלם בין 30 ל־120,
ברירת מחדל 120. ערך לא תקין מונע startup בלי להדפיס את הערך. ההגדרה עוברת
מה־CloudConfig למתאם ול־google-genai במילישניות, וגם ללוג ההגדרות הבטוח.
SDK attempts=1 נשאר; עד שני adapter retries מותרים רק ל־503 UNAVAILABLE כמפורט לעיל.
זהו timeout של לקוח HTTP, לא deadline כולל ל־upload+DB+generation, ולא
הגדרת timeout של Render. אין timeout עצמאי ב־fetch של הממשק ואין כאן
Gunicorn; `--workers 1` אינו כשלעצמו הסבר ל־504. לפי [התיעוד הרשמי של Render](https://render.com/docs/render-vs-heroku-comparison),
תגובות HTTP יכולות להימשך עד 100 דקות; 120 שניות אינן חורגות ממגבלה זו.
ניתוק דפדפן/סינון/רשת, restart או deadline פנימי של הספק עדיין עלולים לקטוע בקשה.
אין מעבר מודל. `gemini-3.8-flash` נשאר. לפי [מדריך ההגירה הרשמי](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8),
הוסרה רק שליחת `temperature=0` למודל זה; מודלים אחרים שומרים על התנהגותם.
זו התאמת API, לא הוכחה לסיבת 503 ולא טענה לשיפור דיוק. JSON mode נשאר,
ו־schema generation מופעל רק אם דגל הניסוי הקיים הוגדר. לא בוצעה הערכת
baseline/holdout חיה; אין לטעון לשימור איכות ללא השוואה מאושרת.

אבחון חדש, בלי תוכן, filenames, hash מסמך, DSN, מפתחות, cookie או review_token:

- `cloud_extraction_config`: booleans של בחירת Gemini/מודל 3.8, נוכחות
  מפתח, outbound/policy/provider approval ו־schema flag; וכן timeout ומספר ניסיונות.
  אלו ערכי התהליך שהפעיל Render, לא קריאה של `.env` מקומי.
- `cloud_upload_started`, `cloud_upload_stage`, `cloud_upload_failed`,
  `cloud_upload_finished`: `request_id`, `run_id` אם כבר נוצר, `stage`,
  `active_stage`, `code`, `status_class`, `elapsed_ms`, `failure_kind`,
  `provider_http_status`, `provider_status` מתוך allowlist בלבד, `timeout_phase`
  ו־`provider_attempt_count`.
  אין stack trace או טקסט exception. הלוג נשלח ל־logger הקיים של Uvicorn.
- כותרת `X-Request-ID` ואובייקט `diagnostic` בתגובת כשל מאפשרים קורלציה.
  לפני יצירת מסמך `run_id=null`; אין חשיפת רשומות אחרות או עקיפת auth.

מה צריך לקבל מהפריסה הבאה, לאחר סקירה ואישור בלבד:

1. `cloud_extraction_config`: כל שערי Gemini ונוכחות המפתח true; schema flag
   לפי הבחירה המפורשת בלבד. היעדר מפתח או מדיניות חסומה מסווגים בנפרד.
2. להעתיק רק `diagnostic` ואת `error.code`/`failure.code`, לא תגובה מלאה.
   `PROVIDER_UNAVAILABLE` יחד עם `provider_http_status=503` ו־UNAVAILABLE
   מאמתים 503 מהספק. `PROVIDER_TIMEOUT`/504 עם provider status 504 הם deadline
   מהספק; `failure_kind=client_timeout` עם status פנימי null הם timeout מקומי,
   ו־`timeout_phase` מבדיל read/connect/write/pool/unknown. לא להסיק מהמספר
   החיצוני בלבד. ראו [שגיאות Gemini](https://ai.google.dev/gemini-api/docs/generate-content/api-errors?hl=en).
3. `CLOUD_DATABASE_NOT_READY`, `CLOUD_DATABASE_CREATE_FAILED` או
   `CLOUD_DATABASE_COMPLETE_FAILED` מצביעים על שלב DB מסוים. לא להריץ migration
   או לשנות הרשאות כתגובה אוטומטית. כשל completion אינו אישור להעלות שוב;
   הכתיבה עלולה להיות לא ודאית ויש לבדוק את הרשומה לפני ניסיון ידני נוסף.
4. `CLOUD_PROVIDER_CLIENT_INIT_FAILED` או `CLOUD_BACKEND_UNEXPECTED` עם stage
   מצביעים על שכבת השרת. כשל parsing/canonical מוחזר כ־502 עם קוד מתאים.
5. אם מתקבל HTML/טקסט 503/504 ללא request ID ובלי רשומת finish, לבדוק
   אירועי Render restart/OOM/proxy בנפרד. Free עשוי לישון או להתאפס לפי
   [מגבלות Render Free](https://render.com/docs/free); לא אומת אירוע כזה באתר החי.

רק לאחר אישור קוד ו־commit/push, המשתמש יבחר פריסה ידנית של ה־commit
המאושר. אין צורך לשנות Neon או מפתח. אפשר להגדיר ב־Backend בלבד
`GEMINI_TIMEOUT_SECONDS=120`, או להשאיר את המשתנה חסר כדי לקבל אותה ברירת מחדל.
לא שונה `render.yaml`; אין שינוי Blueprint לסנכרון. להשאיר Auto Deploy כבוי
ו־Blueprint Auto Sync=No; הודעת ה־commit כוללת `[skip render]`.
לאחר פריסה ידנית של ה־commit החדש, לוודא `sdk_timeout_ms=120000`,
`adapter_retries=2`, `retry_only_http_503_unavailable=true`, `sdk_attempts=1` בלוג `cloud_extraction_config`.
לאחר קריאת לוג ההגדרות,
לבצע בדיקה מורשית עם קובץ סינתטי אחד ואז כמה קבצים נפרדים, ולבדוק בידוד
כשל, ביקורת, אישור אנושי ו־XLSX. אם נמדד timeout מקומי בספק: לשקול שינוי
thinking level או deadline רק כמשימה מדודה ומאושרת, ולא להוסיף retry עיוור.
ה־Blueprint וה־Auto Sync לא שונו במשימת אבחון זו.

מסך הכניסה משתמש כעת בלוגו ובצבעי המוצר; שני פסי הכותרת הוסרו, ומצב
ההתחברות וכפתור היציאה הועברו לכותרת התוכן. ההסכמה ל־Gemini נשארת מפורשת
ולא מסומנת מראש; אזהרת המידע האישי נשארת קצרה באזור ההעלאה והכניסה.

אימות התיקון: Backend offline מלא — **1,200 עברו**, כולל 14 בדיקות
אבחון חדשות ובדיקת המשגר המקומי; Frontend — **54 עברו**; TypeScript
ו־production build עברו על אותם קובצי Frontend ללא שינוי נוסף.
בדיקת דפדפן אמיתי מול HTTPS מקומי עברה ב־1440×1000 וב־390×844: לוגו,
RTL ללא overflow, הצגת/הסתרת סיסמה, היעדר שני הפסים, כפתור יציאה בכותרת,
cookie מאובטח, חסימת anonymous/CSRF, העלאה סינתטית, XLSX וביטול session.
נבדקו חזותית ארבעה צילומי מסך; הם תחת `frontend/storage` המוחרגת מההגשה.
אין בבדיקות אלו PostgreSQL או Gemini חיים. בדיקות PostgreSQL לא הורצו
במשימה זו, כדי לא להתחבר למסד. בשלב האימות הראשוני לא בוצעו staging,
commit או push. בהמשך אושר פרסום המקור לאחר סקירה ובדיקות; לא אושרה פריסה.

## הרצה מקומית: רשומות אינן תנאי לחילוץ

`backend.tools.local_demo:app` מפעיל `DemoWorkflow` בזיכרון. ה־Premium UI
מבקש רשומות בתחילת המסך, אך `GET /api/v1/business-records` דורש
`DurableWorkflow`; לכן במצב זיכרון מתקבל **404 / DURABLE_RECORDS_UNAVAILABLE**.
זו אינה שגיאת Gemini או הוכחה שחסר חיבור למסד הנדרש לחילוץ. `POST
/api/v1/demo-runs` נשאר זמין ומפעיל את Gemini אם כל שערי המדיניות וההסכמה
תקינים. גם ביקורת ו־Excel עובדים בזיכרון; סגירת התהליך/TTL מאבדת את הרשומות.
הרשימה השמורה אינה נתמכת במצב זה. ב־local_demo כשל ספק חוזר כתגובה 201 עם
`status=failed` ו־`failure.code`; במצב cloud הוא מתורגם ל־503/504 מתאים.
לכן אין להסיק הצלחה מ־HTTP 201 בלבד. אין fallback לתוצאה מדומה.
המשגר המקומי משתמש ב־SDK timeout של 60 שניות, לעומת ברירת מחדל 120 בענן;
ההגדרה החדשה היא לענן בלבד. הצלחה מקומית לבדה אינה מוכיחה שהכשל בענן נפתר.

נוספה בדיקה המפעילה את **המשגר המקומי האמיתי ומתאם Gemini עם SDK מדומה**:
רשימה נדחית, חילוץ מצליח, ביקורת ו־XLSX עובדים, והרשימה עדיין נדחית. אין
פנייה למסד או קריאה לספק. השגיאה המקומית אינה סיבת PROVIDER_UNAVAILABLE בענן.

להדגמה מקומית מלאה עם שמירה ופתיחה חוזרת, המשגר הנכון הוא
`backend.tools.durable_demo:create_durable_demo_app --factory`, מול PostgreSQL
מקומי ייעודי שכבר הוכן עם 0001–0003. אין צורך ב־Neon. אם אין מסד מקומי
כזה, להישאר במצב זיכרון; פקודות אלו אינן יוצרות מסד, role, migration או הרשאות.
לא להשתמש במסד העסקי `viral_lab` או ב־`review_test_viral_lab` כמאגר הדגמה.

### PowerShell — Backend, מתוך תיקיית הפרויקט המקורית בחלון חדש

אין לקרוא את `.env` המקורי, אין `--env-file`, ואין להעתיק DSN של Render.
המשגרים קוראים רק את סביבת התהליך. לבחור `memory` לבדיקה ללא מסד; לבחור
`durable` רק אם קיים מסד מקומי מוכן בשם `viral_lab_submission_local_synthetic`.
הסקריפט במצב durable מאמת יעד וסכמה בקריאה בלבד לפני הפעלת השרת.
אין להריץ קובץ אמיתי: הזנת מפתח ודגלים מאפשרת עלות ספק רק לאחר העלאה
והסכמה ידנית. אנחנו לא ביצענו העלאה חיה כחלק מהאימות.

```powershell
Set-Location -LiteralPath '.\storage\submission-release'
$localPython = (Resolve-Path -LiteralPath '..\..\.venv\Scripts\python.exe').Path
$localMode = 'memory' # Change to 'durable' only for the prepared LOCAL synthetic DB.
foreach ($localName in @('REVIEW_DATABASE_DSN','NEON_OWNER_DSN','GEMINI_API_KEY',
    'PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS')) {
    if (Test-Path -LiteralPath "Env:$localName") { Remove-Item -LiteralPath "Env:$localName" }
}
$env:APP_DEPLOYMENT = 'local'
$env:DEMO_DATA_POLICY = 'synthetic-only'
$env:DEFAULT_EXTRACTION_ADAPTER = 'gemini'
$env:GEMINI_MODEL = 'gemini-3.8-flash'
$env:MAX_UPLOAD_BYTES = '5242880'
$env:GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL = 'false'
$env:AI_OUTBOUND_ENABLED = 'true'
$env:ALLOW_EXTERNAL_PHI = 'true'
$env:APPROVED_AI_PROVIDERS = 'gemini'
try {
    if ($localMode -eq 'durable') {
        $env:REVIEW_DATABASE_DSN = [System.Net.NetworkCredential]::new('',
            (Read-Host 'LOCAL synthetic PostgreSQL DSN only' -AsSecureString)).Password
        @'
import os
from sqlalchemy.engine import make_url
from backend.persistence.db import create_review_engine
from backend.persistence.cloud_database import check_business_schema
engine = None
try:
    dsn = os.environ['REVIEW_DATABASE_DSN']
    url = make_url(dsn)
    if (url.drivername not in {'postgresql', 'postgresql+psycopg'}
        or url.host not in {'127.0.0.1', 'localhost', '::1'}
        or url.database != 'viral_lab_submission_local_synthetic'
        or set(url.query) - {'sslmode', 'connect_timeout'}):
        raise ValueError
    engine = create_review_engine(dsn)
    check_business_schema(engine, 'viral_lab_submission_local_synthetic')
except Exception:
    raise SystemExit('LOCAL_SYNTHETIC_TARGET_OR_SCHEMA_NOT_READY') from None
finally:
    if engine is not None:
        engine.dispose()
print('LOCAL_SYNTHETIC_SCHEMA_READY')
'@ | & $localPython -B -
        if ($LASTEXITCODE -ne 0) { throw 'Local validation failed; server was not started.' }
    } elseif ($localMode -ne 'memory') { throw 'Choose memory or durable.' }
    $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('',
        (Read-Host 'Gemini key for synthetic-only local processing' -AsSecureString)).Password
    if ([string]::IsNullOrWhiteSpace($env:GEMINI_API_KEY)) { throw 'A key is required.' }
    if ($localMode -eq 'memory') {
        & $localPython -B -m uvicorn backend.tools.local_demo:app --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
    } else {
        & $localPython -B -m uvicorn backend.tools.durable_demo:create_durable_demo_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
    }
} finally {
    foreach ($localName in @('GEMINI_API_KEY','REVIEW_DATABASE_DSN','APPROVED_AI_PROVIDERS')) {
        if (Test-Path -LiteralPath "Env:$localName") { Remove-Item -LiteralPath "Env:$localName" }
    }
    $env:AI_OUTBOUND_ENABLED = 'false'
    $env:ALLOW_EXTERNAL_PHI = 'false'
}
```

### PowerShell — Premium UI, בחלון חדש נוסף משורש הפרויקט המקורי

```powershell
Set-Location -LiteralPath '.\storage\submission-release'
@'
process.env.VITE_PUBLIC_DEMO = 'false';
process.env.VITE_API_BASE_URL = '';
process.chdir('frontend');
await import('./frontend/node_modules/vite/bin/vite.js');
'@ | node --input-type=module
```

פקודה זו מפעילה Vite עם ערכי התהליך הנכונים, גם ב־Windows PowerShell שמוחק
משתנה סביבה כשמשייכים לו מחרוזת ריקה. אין תלות בערכי VITE ישנים בקובץ מקומי.
ה־proxy הקיים מפנה `/api` ל־`http://127.0.0.1:8000`; לפתוח רק
`http://127.0.0.1:5173`. הממשק שולח `X-Local-Demo: 1`, ולא cookies של הענן.
לעצור שרת ישן באותו port לפני ההרצה; אין להריץ את משגר הענן כמעקף ל־HTTP מקומי.

אם רוצים קובץ דוגמה מקומי מוחרג כגון `.env.local-synthetic`, אלו ערכים בטוחים
כשה־AI כבוי. הקובץ אינו נטען בפקודות לעיל; הן מגדירות מדיניות במפורש בתהליך.
אין לשמור בו מפתח או DSN של production, ואין לשנות את `.env` המקורי:

```dotenv
APP_DEPLOYMENT=local
DEMO_DATA_POLICY=synthetic-only
DEFAULT_EXTRACTION_ADAPTER=gemini
GEMINI_MODEL=gemini-3.8-flash
MAX_UPLOAD_BYTES=5242880
GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL=false
AI_OUTBOUND_ENABLED=false
ALLOW_EXTERNAL_PHI=false
APPROVED_AI_PROVIDERS=
GEMINI_API_KEY=
REVIEW_DATABASE_DSN=
```

## מצב לקראת פרסום הקוד הסופי

המשתמש דיווח שהמיגרציות 0001–0003 הושלמו ושבדיקת הסכמה החזירה
`CLOUD_SCHEMA_READY`. בהמשך דיווח על הצלחת provisioning ב־Neon:
`CLOUD_NEW_RUNTIME_READY: authenticated probes passed; all probe rows rolled back`.
התפקיד החדש לשימוש ב־Render הוא **`viral_lab_app_runtime`**, במסד
`viral_lab_submission_synthetic`. זהו אימות חי שדווח על ידי המשתמש;
במשימת סקירת הקוד והפרסום לא בוצעה התחברות חוזרת או שינוי ב־Neon.

אין להריץ שוב יצירת תפקיד, GRANT/REVOKE או migration לצורך פרסום המקור.
ב־Render יש להזין `REVIEW_DATABASE_DSN` של התפקיד החדש בלבד, לא את
`NEON_OWNER_DSN` ולא חיבור של `neondb_owner` או `viral_lab_demo_runtime`.
מחרוזות חיבור, סיסמאות ומפתחות אינם חלק מהמדריך או מה־commit.

חסם ההרשאות הראשוני נסגר לפי הדיווח. לצורך חילוץ יש להחיל ב־Render את ארבעת
דגלי Gemini ומפתח השרת שלהלן, ולפרוס את הקוד המעודכן. אין רשימת SHA מחייבת:
כל PDF/PNG/JPEG נתמך עד 5 MiB מתקבל לאחר התחברות, CSRF והסכמה לכל קובץ.
התור נשאר סדרתי, עד 30 קבצים; מגבלות הקצב, הקיבולת והביקורת האנושית נשמרו.
בדיקות הספק מדומות בלבד; יש לבדוק HTTPS/readiness והזרימה בענן לאחר הפריסה.
replay סינתטי עדיין אינו נתמך, אך אינו דרוש לחילוץ Gemini אמיתי של מסמך סינתטי.
פירוט התקלות וכלי provisioning בהמשך נשמר כתיעוד וכהוראות למקרה של
מסד חדש בעתיד, ולא כהנחיה לחזור על השינויים שכבר הושלמו.

## ההכרעה לגבי Cookies

המשתמש אישר להחליף את שני שירותי Render בשירות אחד אם זה הפתרון הפשוט
והבטוח. נבחר **Render Free Web Service אחד**: FastAPI מגיש את React הבנוי,
את מסך הכניסה ואת `/api/v1` באותו HTTPS origin. הנתונים נשמרים ב־Neon Free.
אין צורך בדומיין פרטי, בעוגיות צד שלישי, ב־reverse proxy חיצוני או ב־bearer
token ב־localStorage. חסימת Third-Party Cookies אינה פוגעת במסלול זה.

[Render מתעד rewrites](https://render.com/docs/redirects-rewrites) גם ליעדים
בכתובת מלאה, אך לא הסתמכנו עליהם כ־proxy מאומת לכל מתודות ה־API, multipart
ועוגיות. בפרט לא הונח ששינוי URL או SameSite=None פותרים את חסימות הדפדפן.
האימות הוא first-party ונבדק בדפדפן HTTPS מקומי עם חסימת עוגיות צד שלישי.
אין שינוי בהגנות `LocalDemoRoute`, במשגרי localhost או ב־factory המקומי.

## מה נוסף ומה נשמר

- `backend.cloud_app:create_cloud_app --factory` הוא entry point ציבורי חדש
  ומפורש. הוא אינו משתמש במשגר durable_demo הציבורי ואינו מחליף את peer,
  Host או Origin כדי להתחזות ל־localhost. הוא רושם מחדש רק את ה־handlers
  העסקיים הקיימים דרך boundary חדש; החילוץ, הביקורת והייצוא נשארים בשירותים.
- Generic extraction, review API הישן, `/docs`, `/redoc` ו־`/openapi.json`
  אינם נרשמים ביישום הענן. כל נתיב `/api/` אחר דורש session, למעט כניסה
  ו־health/readiness. אין דרך לעקוף אימות בעזרת `X-Local-Demo`.
- כל המוזמנים מקבלים הרשאת reviewer משותפת לאותו dataset סינתטי ייעודי.
  זו אינה מערכת multi-tenant או ACL לפי מסמך/משתמש. הצהרת בודק נשארת
  self-declaration ולא זהות אדם מאומתת. אין להשתמש בה למידע רפואי אמיתי.
- password hash נשמר בשרת בלבד; `python -m backend.tools.demo_password`
  מבקש סיסמה אינטראקטיבית באורך 16–256 תווים ומפיק hash מומלח של scrypt
  עם N=16384,r=8,p=5. מומלצת סיסמה אקראית ארוכה ממנהל סיסמאות.
- session ID אקראי של 256 ביט נשמר ב־cookie בשם `__Host-viral-demo`:
  HttpOnly, Secure, SameSite=Strict, Path=/, ללא Domain, למשך שעה.
  בשרת נשמר רק hash של ה־ID, עם תוקף ו־CSRF token. יציאה מבטלת את ה־session.
- עד 32 sessions בזיכרון של **worker אחד בלבד**. restart/idle spin-down
  מבטלים התחברות, אך אינם מוחקים רשומות ב־Neon. לא להגדיל workers או לשכפל
  instances ללא auth store מתאים. הסיסמה אינה נשמרת בדפדפן אחרי login.
- כל mutation דורש Origin מדויק ו־`X-Demo-CSRF` הקשור ל־session; קריאות API
  דורשות `X-Demo-Request: 1`. ה־CSRF token נמצא בזיכרון JS, אינו session ID.
  strict CORS מאפשר origin יחיד בלבד, ללא wildcard. TrustedHost מגביל Host.
- כניסה מוגבלת גלובלית ל־20 ניסיונות בחמש דקות, גם עם X-Forwarded-For מזויף.
  בדיקות הסיסמה מסודרות כדי להגביל זיכרון. session מוגבל ל־120 בקשות בדקה.
  אלה מגבלות תהליך, המתאפסות ב־restart; אינן שירות הגנה מבוזר מפני DDoS.
- גוף בקשה מוגבל גם ב־stream/chunked, לפני parsing: העלאה 5 MiB ועוד מעטפת
  multipart של 64 KiB, פקודות 128 KiB, login ארבעה KiB. קובץ נבדק לפי חתימה.
  עיבוד העלאה אחד בכל רגע; מתחרה מקבל 429. רק retry הספק המוגבל והמפורש שלעיל מותר.
- Neon: עד 30 מסמכים ו־32 MiB של bytes מקור, גם בכשלים. admission ננעל
  ב־PostgreSQL advisory transaction lock ונבדק באותה עסקה של INSERT.
  snapshot לענן מוגבל ל־64 KiB; התקרה הקיימת של 32 טפסים נשמרת.
  היסטוריית הביקורת אינה נמחקת אוטומטית; צריך לעקוב אחר מכסת Neon.
- קודי provider אמיתיים נשמרים: rate limit → 429, unavailable → 503,
  timeout → 504. גוף השגיאה שומר run_id וכשל שמור, ואינו ממציא רשומה מוצלחת.
  ה־Frontend שומר את capability של ריצת הכשל גם בתשובת HTTP שגויה, ממשיך את
  התור בלי upload חוזר ומאפשר דוח כשל/ייצוא כשל משותף, בלי להציג טופס מאושר.
  status code או קובץ Excel אינם אישור טופס. אין אישור שדה אוטומטי.
- תגובות API הן no-store; אין access log יישומי של URL/מסמך; נוספו CSP,
  nosniff, no-referrer, anti-framing ו־HSTS. לוגי הפלטפורמה עדיין דורשים
  בדיקה לאחר פריסה; אין לכתוב לתוכם סודות או תוכן מסמכים.

## הגדרות Render שנבחרו

ה־Blueprint הוא `render.yaml` בשורש ה־Git branch, **לא** בשורש העץ המקורי
תחת `storage/submission-release` לאחר clone. אין לבצע clone של קובץ `.git`
של linked worktree. הפרסום העתידי צריך לכלול commit מאושר של ענף השחרור.

Web Service:

- Repository: `https://github.com/sh6252/Viral_Lab_Document_Extraction_System`.
  יש לבחור את ענף השחרור ולוודא שה־commit שפורסם נגיש לשופטים; אין להסיק
  נגישות ציבורית רק מהצלחת push של משתמש בעל הרשאה.
- Branch: `release/submission-2026-10-22`; Root Directory: ריק (שורש הריפו).
- Runtime Python; Free; region Frankfurt, רצוי תואם לאזור Neon.
- Python 3.14.3 ו־Node 24.14.0. metadata של הפרויקט תומך ב־Python
  >=3.12,<3.15; הבדיקות המקומיות נעשו ב־Python 3.14/Windows. build Linux
  המדויק ב־Render עדיין מחייב אימות, כולל התקנת dependencies טרייה.
- Build: `npm --prefix frontend ci && npm --prefix frontend run build && python -m pip install .`
- Start:
  `python -m uvicorn backend.cloud_app:create_cloud_app --factory --host 0.0.0.0 --port $PORT --workers 1 --no-access-log --no-proxy-headers`
- Health Check Path: `/api/v1/ready`. `/api/v1/health` הוא liveness נפרד.
- Auto-deploy כבוי ב־Blueprint. יצירת שירות ב־Render עדיין עלולה להתחיל
  deploy ראשון; אין ליצור את השירות לפני אישור פריסה.
- אין migration ב־build או startup. אין דיסק קבצים מקומי מתמשך ואין SQLite.

Render מגדיר את `RENDER_EXTERNAL_URL`; זהו מקור ברירת המחדל המדויק ל־Origin
ול־Host. `DEMO_PUBLIC_ORIGIN` הוא override אופציונלי ל־HTTPS origin מלא,
ללא slash סופי, path, wildcard או credentials. בשירות יחיד אין צורך בו.

## הגדרות Static Site המקוריות — אינן המסלול הפעיל

אם נדרש אתר סטטי נפרד לתצוגה בלבד: Root `frontend`, build
`npm ci && npm run build`, publish `dist`, Node 24.14.0. האפליקציה אינה
משתמשת כיום ב־URL routing; במקרה של הוספת router, rewrite `/*` אל `/index.html`.
`VITE_API_BASE_URL` תומך ב־HTTPS origin של API, ו־`VITE_PUBLIC_DEMO=true`
מוסיף מסך כניסה. אלה ערכים ציבוריים בזמן build בלבד.

**אין לפרוס Static Site נפרד מול backend זה בשני onrender.com שונים**:
ה־boundary המוגדר הוא same-origin וה־cookies הן Strict. התצורה הזו אינה
תחליף עובד להדגמה שנבחרה. לא נוצר שירות static נוסף ב־render.yaml.
אין סיסמה, DSN או Gemini key בשום משתנה VITE_. ב־build הנבחר
`VITE_API_BASE_URL` ריק ו־`VITE_PUBLIC_DEMO=true`.

## משתני סביבה בשרת

ה־Blueprint מספק:

- `APP_DEPLOYMENT=cloud`, `DEMO_DATA_POLICY=synthetic-only`.
- `DEMO_DATABASE_NAME=viral_lab_submission_synthetic`.
- `MAX_UPLOAD_BYTES=5242880`; factory מסרב לתקרה גדולה מחמישה MiB.
- `AI_OUTBOUND_ENABLED=true`, `ALLOW_EXTERNAL_PHI=true`,
  `APPROVED_AI_PROVIDERS=gemini` ו־`DEFAULT_EXTRACTION_ADAPTER=gemini`.
- הגדרות build הציבוריות שפורטו למעלה.

המשתמש מוסיף ב־Render Environment, בלי להכניס לריפו:

- `REVIEW_DATABASE_DSN`: חיבור **direct/unpooled** של `viral_lab_app_runtime` ב־Neon למסד הסינתטי,
  עם `sslmode=require&channel_binding=require`. מתקבל גם scheme
  `postgresql://`/`postgres://`, שמומר ל־`postgresql+psycopg` בלי לאבד TLS.
  host חייב להסתיים ב־`.neon.tech`; `-pooler`, loopback, מסד אחר ופרמטרי
  override של host/dbname נדחים לפני חיבור. בריכת האפליקציה מוגבלת לשני
  חיבורים, connect timeout עשר שניות, statement timeout 15 שניות ו־lock
  timeout חמש שניות. אין חיבור מתוך הפקודה הזו ל־`viral_lab` המקומי.
- `DEMO_PASSWORD_HASH`: הפלט של מחולל הסיסמה ב־Backend. לא הסיסמה הגלויה.
- `GEMINI_API_KEY`: סוד Backend בלבד ב־Render Environment, לעולם לא `VITE_`.
  להגדיר `GEMINI_MODEL=gemini-3.8-flash`, בהתאם לברירת המחדל בקוד וב־Blueprint.
- להסיר את `DEMO_ALLOWED_DOCUMENT_SHA256` הישן מ־Render; הקוד אינו קורא אותו.
  SHA-256 עדיין נשמר לבדיקת שלמות המסמך, ללא הגבלת גישה לרשימת קבצים קבועה.

בשירות Render קיים יש לעדכן את Environment ידנית; הוספת `sync: false`
ל־Blueprint אינה מזריקה סוד לשירות קיים. אין להעתיק מפתח ל־Git או לדפדפן.
`ALLOW_EXTERNAL_PHI=true` הוא שמו של שער המדיניות הקיים הדרוש למתאם;
אינו מתיר העלאת מידע רפואי אישי. `DEMO_DATA_POLICY=synthetic-only` נשאר,
והממשק מזהיר לא להעלות מסמכי מטופלים אמיתיים או מידע רפואי אישי. נדרשת
הסכמה מפורשת שאינה מסומנת מראש; ללא הסכמה אין קריאת ספק. אין זיהוי אוטומטי
שמוכיח שקובץ סינתטי: האחריות לבחירת תוכן סינתטי היא על המשתמש המורשה.
ברירות המחדל המקומיות ב־`.env.example` נשארות עם AI כבוי.

### Gemini 3.8 Flash ותיאום Blueprint

המזהה המדויק הוא `gemini-3.8-flash`. לפי [תיעוד המודל הרשמי](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash),
נתמכים PDF, תמונות ופלט מובנה. SDK מקומי: `google-genai==1.75.0`.
בדיקות ללא רשת מפעילות את ה־SDK האמיתי עם `httpx.MockTransport` עבור PDF,
PNG ו־JPEG, בודקות את נתיב המודל, MIME וה־bytes שנשלחים, JSON mode וגם
`response_json_schema`, ופענוח לתוצאה הקנונית. פלט JSON נבדק תמיד מול חוזה
האפליקציה. `GEMINI_STRUCTURED_OUTPUTS_EXPERIMENTAL` נשאר כבוי כברירת מחדל;
התמיכה בסכמה נבדקת ללא הפעלת ניסוי זה בענן. לא שונו prompt או כללי ביקורת.
אימות עדכון המודל: **31 בדיקות מתאם עברו**, כולל שש בדיקות תעבורת SDK;
בריצת Backend מלאה ללא integration עברו **1,185 בדיקות, ללא כשל או דילוג**.
גם `pip check` עבר. קובצי Frontend לא שונו בעדכון המודל; בדיקות ובניית
ה־Frontend מהתיקון הקודם מתועדות בהמשך.
אין כאן בדיקת זמינות חיה, הרשאות המפתח, מכסה או איכות חילוץ של המודל החדש;
לא בוצעה השוואת baseline/holdout ולא ניתן לטעון לשימור דיוק ללא הערכה חיה מאושרת.

`render.yaml` מגדיר את ארבעת דגלי Gemini ואת `GEMINI_MODEL` עם `value` מפורש.
בעת סנכרון הם יגברו על ערכים ידניים סותרים. להזין ידנית את אותם ערכים בלבד.
מפתח Gemini, DSN ו־password hash נשארים `sync: false`: אין ערכים סודיים בקובץ,
ובשירות קיים הסנכרון לא יעדכן אותם. ראו [כללי משתני Blueprint](https://render.com/docs/blueprint-spec#setting-environment-variables).

**לפני push ללא פריסה**, בשירות מחובר ל־Blueprint יש להגדיר ב־Settings
את `Auto Sync = No`. `autoDeployTrigger: off` מכבה פריסת קוד אוטומטית,
אך אינו מכבה Auto Sync של ה־Blueprint; סנכרון שינוי תצורה יכול להפעיל פריסה.
ראו [כיבוי סנכרון אוטומטי](https://render.com/docs/infrastructure-as-code#disabling-automatic-sync).
אין אפשרות לכבות הגדרת Dashboard זו באמצעות `autoDeployTrigger` ב־YAML.
במשימה זו לא משנים הגדרות Render ולא מפעילים sync/deploy. המשתמש ישלים
את ההגדרות ואת הפריסה לאחר שהקוד מוכן.

## Neon ומיגרציה מפורשת

ב־Neon Console יש ליצור project/branch/database חדשים עם נתונים סינתטיים
בלבד, לא clone/restore של מסד רפואי. בחרו את אותו אזור של Render ככל האפשר.
שם המסד הנוכחי בקונפיגורציה: `viral_lab_submission_synthetic`.
ב־Connect בחרו direct connection (כבו connection pooling), role ו־database
הנכונים, והעתיקו את מחרוזת TLS לשדה הסודי המתאים בלבד.

מומלץ role בעל DDL למיגרציה ו־role runtime נפרד ללא בעלות/הרשאות DDL.
ליצירת runtime מוגבל משתמשים ב־SQL; אין ליצור אותו דרך Console/API שמוסיפים
חברות ב־neon_superuser. אין ליצור סיסמה בתוך קובץ מקור.
במסד חדש בעתיד, לאחר שה־role הלא־privileged בשם `viral_lab_app_runtime` קיים, בעל המסד יכול
להעניק לו במסד החדש בלבד:

```sql
GRANT CONNECT ON DATABASE viral_lab_submission_synthetic TO viral_lab_app_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO viral_lab_app_runtime;
GRANT SELECT ON alembic_version TO viral_lab_app_runtime;
GRANT SELECT, INSERT ON business_documents, business_records, business_review_events TO viral_lab_app_runtime;
GRANT UPDATE (status, extraction_id, canonical_snapshot_json, canonical_snapshot_sha256,
              failure_json, failure_sha256) ON business_documents TO viral_lab_app_runtime;
GRANT UPDATE (revision, review_status, projection_json, projection_sha256)
  ON business_records TO viral_lab_app_runtime;
```

אין להעניק owner/superuser/neon_superuser, DELETE או schema CREATE ל־runtime.
בדיקת הרשאות runtime ב־Neon הצליחה לפי דיווח המשתמש; עדיין יש לאמת את
חיבור המוצר עצמו לאחר פריסה עם הסודות שהוגדרו ב־Render.
תפקיד בעל הרשאות מוגברות שנוצר ב־Console אינו מתאים ליישום. במקרה הנוכחי
נוצר תפקיד SQL חדש ומוגבל; התפקיד הישן נשאר ללא שינוי.

### כלי מקומי להענקת הרשאות — פירוט והיסטוריית הכנה

אם כפתור Run בעורך Neon חסום, אפשר להשתמש ב־
`backend.tools.cloud_runtime_permissions`, לאחר אישור מפורש נפרד לשינוי הרשאות.
הכלי אינו מופעל מ־Render, מ־startup או ממיגרציה. אין לו תלות חדשה, והוא
אינו טוען `.env` או משתמש ב־`REVIEW_DATABASE_DSN` של היישום. הוא קורא רק
`NEON_OWNER_DSN` מהסביבה, ואינו מקבל DSN בארגומנט שמופיע בהיסטוריית shell.

לפני כל GRANT/REVOKE הוא מחייב אישור מילולי למסד
`viral_lab_submission_synthetic`, חיבור direct ל־Neon עם TLS ו־channel binding,
username של `neondb_owner`, וזהות חיבור ובעלות מסד תואמות. הוא בודק גם TLS
פעיל בצד הלקוח דרך Psycopg/libpq, את `0003_form_approval` והאילוצים/guards דרך שער הסכמה
הקיים, באותה עסקה. הוא אינו מריץ migration. מצבי ACL ואבחון קוראים קטלוגים;
מצבי create/verify כוללים probes סינתטיים בחיבור runtime כמפורט בהמשך.

התפקיד `viral_lab_demo_runtime` חייב להתקיים עם LOGIN, ללא SUPERUSER,
CREATEDB, CREATEROLE, REPLICATION או BYPASSRLS, ללא חברות בתפקיד אחר
(לרבות `neon_superuser`, גם אם אינה inherited), וללא בעלות על מסד או אובייקטים.
אם אינו מתאים, הכלי עוצר לפני שינוי. מצבי ACL ואבחון **אינם** יוצרים/מוחקים
תפקיד, משנים סיסמה או מסירים membership. יצירת התפקיד החדש מחייבת מצב
create מפורש כמפורט בהמשך, ואינה משנה את התפקיד הישן.
[Neon מתעד](https://neon.com/docs/manage/roles) שתפקיד שנוצר ב־Console/API/CLI
עשוי לקבל חברות ב־`neon_superuser`; לכן עצם יצירתו ב־Console אינה הוכחה שהוא מוגבל.

הכלי מבצע בדיוק את שבע פקודות ההרשאות שלמעלה, עם שמות טבלאות מפורשים
ב־public, ללא `ALL`, DELETE, בעלות, grant option, הרשאת CREATE או הרשאה
לטבלאות עתידיות. ה־REVOKE המתועד מסיר CREATE מ־PUBLIC בסכמת public של
המסד הזה, ולכן משפיע גם על תפקידים אחרים שהסתמכו עליו באותו מסד.

בדיקות `has_*_privilege` מאמתות הרשאות אפקטיביות, כולל PUBLIC, ברמת מסד,
schema, טבלה ועמודה. הן חוסמות UPDATE גורף, UPDATE לעמודות המקור, DELETE,
TRUNCATE, REFERENCES, TRIGGER, MAINTAIN (ב־PG17+), grant options וגישה עודפת
לטבלאות/רצפים של משתמש במסד. חסר בהרשאות הרצויות מותר בבדיקת ההכנה בלבד.
הרשאת CREATE ל־PUBLIC ב־public מותרת לפני השינוי בלבד; CREATE ישיר ל־runtime
נחסם. הרשאות ברירת מחדל של PUBLIC ל־TEMP/CONNECT במסד ול־EXECUTE בפונקציות
אינן משתנות; הכלי אינו ביקורת אבטחה גלובלית של כל הפונקציות או של מסדים אחרים.

לאחר הפקודות נבדקים שוב זהות/תפקיד/סכמה וכל ההרשאות האפקטיביות באותה
עסקה. כשל מבטל את **כל שינויי ה־ACL באותה עסקה**. כשל רשת בזמן commit
עשוי להשאיר תוצאה לא ודאית ללקוח; במצב כזה יש להריץ `--check` לקריאה בלבד.
מודפסים קודי תוצאה קבועים בלבד, ללא סיסמה, DSN או SQL exception.

ב־Neon Connect יש לבחור ידנית branch בשם `production`, database בשם
`viral_lab_submission_synthetic`, role בשם `neondb_owner`, ולכבות pooling.
שם branch של Neon אינו חשוף דרך שם מסד PostgreSQL; הכלי מאמת את יעד ה־DSN
אך אינו מאמת את שם הענף דרך Neon API. אין להעתיק את DSN הבעלים ל־Render.

PowerShell משורש הפרויקט המקורי, **לשימוש רק לאחר אישור לשינוי הרשאות**:

```powershell
Set-Location .\storage\submission-release
$runtimeGrantPython = (Resolve-Path ..\..\.venv\Scripts\python.exe).Path
try {
    $env:NEON_OWNER_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'Neon production / neondb_owner direct TLS DSN' -AsSecureString)).Password
    & $runtimeGrantPython -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --apply
    if ($LASTEXITCODE -ne 0) { throw 'Runtime permissions were not confirmed; inspect the fixed error code.' }
} finally {
    Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue
}
```

אם כבר נמצאים בעץ השחרור, יש לדלג על `Set-Location`. ה־DSN מוזן בהסתרה
ונשאר בזיכרון התהליך/סביבתו בזמן ההרצה, לא בקובץ מקור או בהיסטוריית הפקודות.
לבדיקת הכנה ללא שינויים יש להסיר `--apply`; מתקבל
`CLOUD_RUNTIME_PREFLIGHT_OK: no permissions changed` אם הבדיקות עברו.
לאימות כל ההרשאות בלבד יש להחליף אותו ב־`--check`. מצבי הקריאה בלבד
מגדירים transaction read-only. אחרי apply/check מוצלח מודפס
`CLOUD_RUNTIME_PERMISSIONS_READY`; חסר או עודף בהרשאות מחזיר exit code 2.
אימות זה נעשה מקומית מבעל המסד באמצעות קטלוגי PostgreSQL, ואינו בדיקת
התחברות מרשת Render. יש לשמור ב־Render רק את ה־DSN הנפרד של runtime.

#### אבחון שגיאת זהות/TLS ללא שינוי הרשאות

בגרסה הראשונה `_preflight` השווה חמישה ערכים יחד:
`(current_database, current_user, session_user, database_owner, pg_stat_ssl.ssl)`
אל `(viral_lab_submission_synthetic, neondb_owner, neondb_owner, neondb_owner, true)`.
כל אי־התאמה החזירה `DATABASE_OWNER_OR_TLS_IDENTITY_REJECTED`. זו שגיאת
אבחון בכלי: הקוד הישן אינו מאפשר לדעת איזה ערך נכשל. בדיקות membership
של runtime והסכמה נמצאות אחריה, ולכן אינן מקור קוד השגיאה הזה. הצלחת
`cloud_migrate --check` אינה מוכיחה התאמה של חמש בדיקות אלה.

בנוסף, `pg_stat_ssl` מתאר את חיבור ה־backend של PostgreSQL. עם proxy הוא
אינו בהכרח מתאר את חיבור המחשב ל־Neon. לכן אין להשתמש בו לבדו כהוכחה
ל־TLS של הלקוח. התיקון בודק את `connection.driver_connection.pgconn.ssl_in_use`
של Psycopg, המייצג את `PQsslInUse` של libpq בחיבור הפעיל. נדרשים עדיין
host ישיר של Neon, מסד מדויק, `sslmode=require`/`verify-full` ו־
`channel_binding=require`. TLS לא פעיל או מצב driver לא ידוע חוסמים פעולה,
גם אם `pg_stat_ssl` מדווח true. אין fallback לדגלי DSN בלבד.

מצב `--diagnose` מחזיר JSON עם boolean/null וקודים קבועים בלבד, ללא
שמות שהגיעו מהשרת, endpoint, DSN, פרמטרי SQL או exception. זהו transaction
read-only שמבצע אבחון זהות ו־TLS בלבד; הוא אינו מאמת הרשאות runtime או
סכמה ואינו מריץ GRANT/REVOKE. לא ניתן לשלבו עם `--apply` או `--check`.

ה־JSON כולל `checks`, `blocking_codes` ו־`legacy_failure_codes`, המראים
גם אילו חלקים בתנאי הישן היו נכשלים. הקודים העדכניים מבחינים בין:

- `CONNECTED_DATABASE_MISMATCH` — המסד הפעיל שונה מהמסד המאושר.
- `CONNECTED_CURRENT_USER_MISMATCH` — תפקיד הביצוע שונה מ־neondb_owner.
- `CONNECTED_SESSION_USER_MISMATCH` — זהות ההתחברות שונה מ־neondb_owner.
- `DATABASE_OWNER_MISMATCH` — בעל המסד אינו neondb_owner; אין שינוי בעלות אוטומטי.
- `CLIENT_TLS_NOT_ACTIVE` — מנהל ההתקן מדווח שאין TLS פעיל בחיבור הלקוח.
- `CLIENT_TLS_STATUS_UNAVAILABLE` — אי אפשר לאמת TLS דרך מנהל ההתקן; חסימה בטוחה.

אם רק `backend_tls_active` הוא false/null בעוד `client_tls_active` true
וכל בדיקות הזהות תואמות, `legacy_failure_codes` יכלול
`BACKEND_TLS_NOT_ACTIVE`/`BACKEND_TLS_STATUS_UNAVAILABLE`; זה מזהה כשל של
הנחת ה־TLS הישנה בכלי. מצב backend זה אינו משמש לעקיפת TLS של הלקוח.
אם `database_owner_matches` false, נדרש בירור בעלות/authority לפני תיקון נוסף.
חברות המנהל ב־neon_superuser מותרת ומדווחת כ־boolean; היא אינה פוטרת אותו
מבדיקת בעלות ואינה מתירה חברות דומה ל־runtime. אין ראיה מקומית מהשגיאה
הישנה בלבד שהבעלות שגויה, ולכן לא הורחבה הרשאת הבעלים למנהל כלשהו.

להרצה ידנית של אבחון בלבד, מתוך עץ השחרור (לא הורץ מול Neon במסגרת התיקון):

```powershell
try {
    $env:NEON_OWNER_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'Neon production / neondb_owner direct TLS DSN' -AsSecureString)).Password
    & ..\..\.venv\Scripts\python.exe -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --diagnose
} finally {
    Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue
}
```

אפשר לשתף את פלט ה־JSON בלבד, ללא מחרוזת החיבור. Exit code 2 מציין חסימת
זהות/TLS; exit code 0 מציין השלמת אבחון ללא חסימת זהות, **לא** אישור הרשאות.
בדיקות הכלי מדמות TLS של לקוח/backend באופן עצמאי, זהויות שגויות, מנהל
Neon מורשה, runtime מוגבר, פלט ללא סודות ואיסור שילוב מצבי אבחון/שינוי.
לא בוצעו חיבור Neon, שינוי הרשאות, מיגרציה או קריאת Gemini לצורך האבחון.

מקורות: [משמעות pg_stat_ssl ב־PostgreSQL](https://www.postgresql.org/docs/current/monitoring-stats.html#MONITORING-PG-STAT-SSL-VIEW),
[PQsslInUse של libpq](https://www.postgresql.org/docs/current/libpq-status.html#LIBPQ-PQSSLINUSE),
[גישה ל־driver connection ב־SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.ManagesConnection.driver_connection)
ו־[ניהול תפקידי Neon](https://neon.com/docs/manage/roles).

#### אבחון מדויק של תפקיד runtime — קריאה בלבד

הודעת `RUNTIME_ROLE_MISSING_OR_PRIVILEGED` נובעת מהשוואת שורת התפקיד
לדרישה `LOGIN=true` ו־`SUPERUSER=CREATEDB=CREATEROLE=REPLICATION=BYPASSRLS=false`,
או מהיעדר התפקיד. הבדיקה אינה בודקת בעלות או membership; אלה נבדקים רק
בהמשך. לכן `Owns: -` ב־Dashboard אינו מסביר או שולל את השגיאה הזו.

מצב `--diagnose-runtime` מוסיף אבחון מבודד של מאפייני תפקיד וחברויות.
אחרי אימות יעד Neon, זהות בעלים ו־TLS פעיל של הלקוח, הוא קורא רק קטלוגי
PostgreSQL בעסקה read-only. אין בו GRANT, REVOKE, SET ROLE או ALTER ROLE.
לא ניתן לשלב אותו עם `--apply`, `--check` או `--diagnose`. כללי ה־preflight
והרשאות היישום עצמם אינם משתנים.

הפלט מכיל boolean וקודים קבועים בלבד, ללא שמות תפקידים שהגיעו מהשרת,
OID, DSN, סיסמה או פרטי SQL. בין הקודים:

- `RUNTIME_ROLE_MISSING` — התפקיד לא נמצא ביעד המאומת.
- `RUNTIME_ROLE_LOGIN_DISABLED` — התפקיד אינו רשאי להתחבר.
- `RUNTIME_ROLE_SUPERUSER` — דגל SUPERUSER פעיל בתפקיד עצמו.
- `RUNTIME_ROLE_CREATEDB`, `RUNTIME_ROLE_CREATEROLE`, `RUNTIME_ROLE_BYPASSRLS`
  ו־`RUNTIME_ROLE_REPLICATION` — כל מאפיין מוגבר מדווח בנפרד.
- `RUNTIME_ROLE_NEON_SUPERUSER_MEMBER` — חברות ישירה או עקיפה ב־neon_superuser.
- `RUNTIME_ROLE_INHERITS_PRIVILEGED_ROLE_PERMISSIONS` — הרשאות של תפקיד אב
  שסווג כמוגבר זמינות ל־runtime לפי `pg_has_role(..., 'USAGE')`.
- `RUNTIME_ROLE_CAN_SET_PRIVILEGED_ROLE` — אפשרות לעבור לתפקיד אב מוגבר
  לפי `pg_has_role(..., 'SET')` ב־PG16+, בלי לבצע SET ROLE בפועל. ב־PG14/15
  הבדיקה משתמשת בכלל membership הישן ומכבדת את מגבלת מעבר לתפקיד superuser.
- `RUNTIME_ROLE_HAS_MEMBERSHIPS` — חברות כלשהי; המדיניות הקיימת חוסמת גם
  חברות בתפקיד רגיל, ולא מסירה אותה אוטומטית.

`legacy_role_gate_failed` מציין אם התפקיד היה נכשל בתנאי שהחזיר את
`RUNTIME_ROLE_MISSING_OR_PRIVILEGED`. אם false וקיימים רק קודי membership,
זו חסימה בשלב הבא, ולא סיבת הודעת המאפיינים הישנה. כל הסיבות הזמינות
מוחזרות יחד, ולא רק הראשונה.

החברויות נבדקות בשרשרת רקורסיבית של `pg_auth_members`, עם UNION שמונע
כפילויות ומחזוריות. הפלט מבחין בין חברות ישירה ועקיפה ב־neon_superuser,
ובין עצם החברות, ירושה זמינה ואפשרות SET ROLE. `rolinherit=true` לבדו אינו
סימן להרשאה מוגברת: מ־PG16 אפשרויות הירושה נמצאות גם בכל קשת membership.
מאפיינים מיוחדים כגון SUPERUSER/CREATEDB/CREATEROLE/BYPASSRLS אינם מועתקים
לתפקיד בגלל ירושה. הדיווח על ירושה מתייחס להרשאות הזמינות מתפקיד האב,
ולא לטענה שהדגל CREATEDB של runtime השתנה ל־true.

סיווג תפקידי אב כולל את neon_superuser, תפקידים בעלי מאפיינים מוגברים
ותפקידי PostgreSQL מובנים לגישה רחבה לנתונים/קבצים, הרצת תוכניות ותחזוקה.
זהו אבחון מאפייני תפקיד ונתיבי membership, לא audit מלא של כל ACL מותאם,
כל הבעלויות או הרשאות היישום. exit code 0 אינו אישור readiness לפריסה.

מתוך `storage/submission-release`, עם DSN הבעלים הקיים ב־`NEON_OWNER_DSN`:

```powershell
if ([string]::IsNullOrWhiteSpace($env:NEON_OWNER_DSN)) {
    throw 'Set NEON_OWNER_DSN to the existing neondb_owner direct TLS DSN without printing it.'
}
& ..\..\.venv\Scripts\python.exe -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --diagnose-runtime
```

אין להדפיס את משתנה הסביבה. אין fallback ל־REVIEW_DATABASE_DSN או ל־`.env`.
הפקודה אינה משנה או מוחקת את DSN הבעלים הקיים. JSON האבחון ניתן לשיתוף;
exit code 2 מציין סיבת חסימה. לא הורץ אבחון חי מול Neon במסגרת ההכנה.

#### יצירת runtime דרך SQL — תיקון סיסמה תואם Neon

**חקירת חסם logging ב־Neon:** בקובץ הנוכחי הקוד המלא שהיה מוחזר הוא
`NEW_RUNTIME_PRECOMMIT_PASSWORD_LOGGING_GUARD_INSUFFICIENT_PRIVILEGE`.
`COMMIT_PASSWORD_LOGGING_GUARD_INSUFFICIENT_PRIVILEGE` לבדו אינו קוד נפרד
בקובץ הזה. `PRECOMMIT` מתאר שלב לפני COMMIT; אינו דיווח שה־COMMIT התחיל.

הסדר הוא `engine.begin()` → בדיקות יעד/בעלים/TLS/סכמה/היעדר תפקיד →
guard → CREATE ROLE → GRANT → בדיקות הרשאות → יציאה מה־with שמבצעת COMMIT.
SQLSTATE 42501 ב־guard נוצר בתוך העסקה, **לפני CREATE ROLE ולפני GRANT**.
החריגה יוצאת מה־with ומפעילה rollback; אין ניסיון COMMIT במסלול הכשל הזה.
באותה הרצה לא נוצר תפקיד, אך ייתכן תפקיד מריצה אחרת ולכן יש לבדוק קיום.

ה־guard התחיל ב־`SET LOCAL log_statement = 'none'`, והמשיך בין השאר עם
`log_min_error_statement`, `log_min_messages` והגדרות duration/sample.
[PostgreSQL דורש superuser או הרשאת SET מתאימה](https://www.postgresql.org/docs/16/runtime-config-logging.html)
עבור הגדרות אלה. חברות `neondb_owner` ב־neon_superuser אינה SUPERUSER
מקורי ואינה מבטיחה SET על GUC מוגן. זו דרישת־יתר של הכלי לסביבת Neon.
מהקוד הישן המאוחד אי אפשר להוכיח איזו הגדרה נדחתה בפועל; log_statement
הוא המועמד הראשון לפי הסדר. לא בוצע חיבור למסד לצורך בירור זה.

כעת כשל guard כולל מזהה קבוע של ההגדרה, למשל
`NEW_RUNTIME_PRECOMMIT_PASSWORD_LOGGING_GUARD_LOG_STATEMENT_INSUFFICIENT_PRIVILEGE`.
כשל ב־`password_encryption` מסווג בנפרד תחת `PRECOMMIT_PASSWORD_POLICY`.
כשל COMMIT אמיתי נשאר `NEW_RUNTIME_COMMIT_OUTCOME_UNCERTAIN_*`.
אין פלט של הודעת שרת, ערך סיסמה, DSN, SQL מלא או verifier.

**התיקון המצומצם:** ברירת המחדל המחמירה נשמרת, בלי fallback אוטומטי.
למסלול SQL הנתמך ב־Neon, שאינו משנה הגדרות logging, נוספה בחירה מפורשת
`--acknowledge-server-password-logging-risk`, המותרת רק עם `--create-runtime`.
הכלי מודיע על הסיכון לפני בקשת הסיסמה. במצב זה נשארת רק הגדרת
password_encryption מקומית ל־SCRAM; כל הגנות היעד, TLS, תפקיד מוגבל,
שבע ההרשאות, התחברות עצמאית ו־rollback של probes נשמרות.

הבחירה היא **קבלת סיכון**, לא מנגנון למניעת רישום בצד השרת. הסיסמה בתוך
CREATE ROLE עשויה להישמר בלוגי שגיאות/SQL, audit או ניטור ספק, גם כש־TLS
פעיל והלקוח אינו מדפיס דבר. [PostgreSQL מזהיר על כך במפורש](https://www.postgresql.org/docs/16/sql-createrole.html).
אין כאן הבטחה ש־Neon שומר או אינו שומר בפועל את הפקודה. אם דרישת אפס
סיסמאות בלוגי שרת היא קשיחה, **אין להריץ יצירה במצב זה**; נדרש אישור
מספק הענן או מסלול credential provisioning אחר שאומת עבור תפקיד מוגבל.

נבדק התיעוד של יצירה ב־Console/API ושל
[reset password דרך API](https://api-docs.neon.tech/reference/resetprojectbranchrolepassword).
יצירת תפקיד ב־Console/API מוסיפה neon_superuser ולכן אינה תחליף מתאים.
קיים reset לתפקיד קיים, אך לא אומת מסלול לאתחול סיסמה ראשונה של תפקיד
SQL בלי סיסמה תוך שימור כל הדגלים והחברויות. הוא לא הוטמע או הוצג כפתרון
מוכח. למסלול SQL המוגבל והמתועד כאן, Neon דורש את הסיסמה המקורית.

**אין להמליץ על ניסיון יצירה נוסף באופן אוטומטי.** תחילה יש להריץ רק
בדיקת קיום, עם DSN הבעלים הקיים ומתוך עץ השחרור:

```powershell
try {
    if ([string]::IsNullOrWhiteSpace($env:NEON_OWNER_DSN)) {
        throw 'NEON_OWNER_DSN must contain the existing direct TLS owner DSN.'
    }
    & ..\..\.venv\Scripts\python.exe -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --runtime-role viral_lab_app_runtime --diagnose-runtime
    # Inspect runtime_role_exists. Exit 2 with RUNTIME_ROLE_MISSING is expected if absent.
} finally {
    Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue
}
```

רק אם התפקיד חסר, ולאחר אישור מפורש לסיכון ולביצוע מול Neon, ניתן לשקול
ניסיון יחיד במצב הנתמך. אם הוא קיים אין לאפס, למחוק או ליצור מחדש.

נמצא ותוקן באג באסטרטגיית הסיסמה: הקוד הקודם שלח SCRAM verifier בתור PASSWORD.
[תיעוד Neon הרשמי](https://neon.com/docs/manage/roles#manage-roles-with-sql)
מציין שסיסמאות hashed אינן נתמכות במסלול SQL. התיקון מעביר את הסיסמה
המקורית מתוך prompt מוסתר, כפרמטר `%s` של `psycopg.ClientCursor`, אחרי
אימות היעד, TLS, הבעלים והסכמה. הוסרה החסימה הזמנית
`NEW_RUNTIME_CREATE_BLOCKED_NEON_PREHASHED_PASSWORD_UNSUPPORTED`.
אין ניסיון חוזר אוטומטי ואין שינוי בתפקיד הישן. התיקון נבדק מקומית במוקים;
לא בוצעה יצירה או התחברות ל־Neon במסגרת הפיתוח.

השגיאה הישנה `NEW_RUNTIME_CREATE_FAILED_OR_COMMIT_UNCERTAIN` איחדה כשל
בפתיחת חיבור, יצירת SCRAM מקומית, CREATE, GRANT וב־COMMIT. היעדר התפקיד
כעת אינו מזהה בדיעבד איזה שלב נכשל. אי־תמיכה ב־verifier היא הסבר מוביל,
אך אין ראיה שזה השלב המדויק שנכשל בהרצה הקודמת.

[מימוש DDL הרשמי של Neon](https://github.com/neondatabase/neon/blob/fa504217c61bbcaf5c512d75830564541f917f8f/pgxn/neon/neon_ddl_handler.c)
אוסף שינויי תפקידים במהלך העסקה. `NeonXactCallback` מפעיל
`SendDeltasToControlPlane` ב־`XACT_EVENT_PRE_COMMIT`; כשל HTTP יכול להכשיל
את ה־commit. `ConstructDeltaMessage` מעביר גם את ערך PASSWORD מהפקודה.
זו אינה ראיה לאיסור כללי על CREATE ROLE ו־GRANT באותה עסקה. לא פוצלה העסקה
ולא שונו הגנות TLS או הגדרות סנכרון Neon. אין להניח אטומיות מלאה של שירות
הבקרה החיצוני רק בגלל rollback מקומי.

קודי הכשל הממוקדים נשמרים במסלול המתוקן:

- `NEW_RUNTIME_PRECOMMIT_PASSWORD_LOGGING_GUARD_<SETTING>_*` — אי אפשר להשתיק רישום
  SQL באופן מקומי לעסקה; נעצרים לפני שליחת הסיסמה, ללא CREATE או GRANT.
- `NEW_RUNTIME_PRECOMMIT_CREATE_ROLE_*` — כשל ביצירת התפקיד.
- `NEW_RUNTIME_PRECOMMIT_GRANT_01_*` עד `GRANT_07_*`, לפי שבע הפקודות
  המתועדות; הפקודה השנייה היא REVOKE של PUBLIC CREATE.
- `NEW_RUNTIME_COMMIT_OUTCOME_UNCERTAIN_*` — יציאה מ־engine.begin. אחרי
  כשל זה אין retry, ניסיון התחברות runtime או שינוי תפקיד אוטומטי.
- `NEW_RUNTIME_VERIFY_LOGIN_*`, `VERIFY_IDENTITY_TLS_*`, `VERIFY_ROLE_*`,
  `VERIFY_SCHEMA_*`, `VERIFY_EFFECTIVE_PERMISSIONS_*`, `VERIFY_PROBE_*`
  ו־`VERIFY_PROBE_ROLLBACK_*` — כשל בבדיקה שאחרי commit או במצב verify.
  תוצאת ניסיון NOLOGIN נשמרת יחד עם קוד שלב האימות המקורי.

הסיומת ממופה רק מ־SQLSTATE מוכר לקוד קבוע כגון `INSUFFICIENT_PRIVILEGE`,
`TRANSACTION_BLOCK_REJECTED`, `SERVER_INTERNAL_ERROR`, `CONNECTION_FAILED`
או `FAILED`. אין חשיפה של הודעת מסד, HTTP response, SQL, parameters,
password, verifier או DSN. `SERVER_INTERNAL_ERROR` לבדו אינו מוכיח כשל
בשירות הבקרה. כל בדיקות היעד, הסכמה והתפקיד המוגבל נשמרות.

כאשר `viral_lab_demo_runtime` נוצר ב־Dashboard עם הרשאות מוגברות, המסלול
הממוקד הוא יצירת **`viral_lab_app_runtime`** דרך חיבור PostgreSQL ישיר.
התפקיד הישן נשאר כפי שהוא: אין DROP, שינוי סיסמה או הסרת membership שלו.
הכלי תומך רק בשני השמות האלה באמצעות `--runtime-role`; ברירת המחדל נשארת
התפקיד הישן לצורך תאימות ואבחון. אין להריץ `--apply` לתפקיד מוגבר.

`--create-runtime --runtime-role viral_lab_app_runtime` מחייב אישור מדויק
למסד הסינתטי, DSN של neondb_owner ב־Neon direct TLS, זהות ובעלות תואמות,
TLS פעיל של הלקוח וסכמה מוכנה. הוא מסרב אם התפקיד החדש כבר קיים, ללא
איפוס סיסמה, דריסה או ניסיון "לתקן" אותו. היצירה מפורשת:

```sql
CREATE ROLE viral_lab_app_runtime WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD %s;
```

זהו template להמחשה בלבד, לא פקודה להעתקה. הסיסמה מועברת בנפרד ל־execute;
אין לשים סיסמה או verifier בקוד/SQL Editor.
באותה עסקה נבדקים מחדש מאפייני התפקיד והיעדר חברות/בעלות, ומוענקות רק
שבע פקודות ההרשאות המתועדות לעיל, לתפקיד החדש. אין GRANT על כל הטבלאות,
הרשאות עתידיות, DELETE, בעלות או DDL. ה־REVOKE היחיד נשאר הסרת CREATE
מ־PUBLIC בסכמת public. כל כשל לפני commit מבטל את התפקיד ושינויי ה־ACL
באותה עסקה; כשל רשת בזמן commit דורש אבחון כי תוצאת הלקוח עשויה להיות לא ודאית.

סיסמה אקראית באורך 32–256 תווי ASCII מודפסים ללא רווחים יש ליצור ולשמור
**מראש במנהל סיסמאות**. הכלי מבקש אותה בהסתרה, פעמיים ביצירה, ואינו מקבל
אותה ב־argv, במשתנה סביבה או בקובץ. אם המסוף אינו מאפשר קלט ללא echo,
ההרצה נכשלת. אין pre-hashing מקומי. PostgreSQL אינו תומך בפרמטרי שרת
ב־CREATE ROLE; [ClientCursor של Psycopg](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#client-side-binding-cursors)
מבצע quoting ו־binding בצד הלקוח. הקריאה משתמשת ב־`execute(template,
(password,))`, ללא חיבור מחרוזות ידני או mogrify. בפועל SQL המכיל סיסמה
נשלח לשרת בתוך חיבור TLS, כפי ש־Neon דורש. הוא אינו עובר ב־SQLAlchemy
statement logging ואינו מודפס או נכתב לקובץ. שגיאות מוחזרות כקודים קבועים.

בברירת המחדל המחמירה, לפני שליחת הסיסמה מוגדרים **לעסקה בלבד**: השתקת statement/error/duration/
sample logging, והשבתת SQL capture של pg_stat_statements ו־pgaudit אם
הגדרותיהן נטענו. גם `password_encryption` מוגדר מקומית ל־scram-sha-256,
כדי שהשרת ישמור את הסיסמה בפורמט SCRAM הנתמך. אם לבעלים אין סמכות
להחיל את ההגדרות, הכלי נכשל לפני CREATE. אין שינוי GUC גלובלי, ALTER ROLE
להגדרות או הענקת סמכות נוספת. ההגדרות חוזרות למצב הקודם ב־commit/rollback.
אין fallback לאחר חסם זה. במצב Neon עם אישור סיכון מפורש אין שינוי של
הגדרות logging מלכתחילה; אין ניסיון להוסיף SET privileges לבעלים.

לא ניתן להבטיח מחיקה פיזית של מחרוזות מזיכרון Python או שליטה על ניטור
חיצוני של ספק הענן, לכידת תעבורה בצד השרת או debug instrumentation שהותקן
מחוץ לכלי. אין להפעיל אותו עם tracing/debuggers שמקליטים SQL או משתנים.
לוגים אינם מקום לסיסמאות; אין לשתף לוגי מסד גולמיים במקרה כשל.

אחרי commit הכלי מתחבר **באמת כ־viral_lab_app_runtime**, עם הסיסמה החדשה
לאותו host/database/TLS, ולא באמצעות SET ROLE בחיבור הבעלים. הוא מאמת
current_user/session_user, TLS, תפקיד מוגבל, סכמה והרשאות אפקטיביות.
בחיבור הזה הוא מבצע SELECT/INSERT/UPDATE נדרשים באמצעות רשומות
permission-probe סינתטיות בשלוש טבלאות העסק. אין extraction, LIVE provenance
או אישור טופס. הוא דורש SQLSTATE 42501 עבור DELETE בכל שלוש הטבלאות,
CREATE SCHEMA, CREATE TABLE ב־public, UPDATE ל־source_bytes ו־UPDATE ל־audit.
כשל trigger או FK אינו נחשב הוכחה לחסימת הרשאה. כל ניסיון אסור עטוף ב־
savepoint עם rollback, וכל עסקת הבדיקה **תמיד מסתיימת ב־rollback**, גם בהצלחה.
לא נשמרים מסמכי בדיקה או שינויים בסכמות. הרשאות ברירת מחדל ל־CONNECT,
TEMP ו־EXECUTE אינן משתנות, ואין שינוי במדיניות PUBLIC במסדים אחרים;
בדיקות ה־DDL מתייחסות למסד הסינתטי ולסכמות העסק המתמשכות.

אי אפשר לבדוק התחברות נפרדת לתפקיד שטרם בוצע לו commit. לכן אם בדיקת
החיבור שאחרי היצירה נכשלת, הכלי מנסה להגדיר **NOLOGIN לתפקיד החדש בלבד**,
לאחר אימות חוזר של הבעלים והתאמת OID לתפקיד שנוצר באותה הרצה. הוא לא
מוחק אותו, לא נוגע בתפקיד הישן ולא מבצע cleanup לתפקיד קיים שהוחלף בינתיים.

- `CLOUD_NEW_RUNTIME_READY` — התחברות ובדיקות הרשאה עברו; רשומות probe בוטלו.
- `NEW_RUNTIME_ALREADY_EXISTS_NO_CHANGES` — אין יצירה/דריסה. יש לאבחן את הקיים.
- `NEW_RUNTIME_CREATE_FAILED_OR_COMMIT_UNCERTAIN` — קוד היסטורי מאוחד;
  החליף אותו הדיווח הממוקד שלעיל. אין להסיק ממנו באיזה שלב הייתה תקלה.
- `NEW_RUNTIME_VERIFY_FAILED_ROLE_DISABLED` — הבדיקה נכשלה; NOLOGIN החדש אושר.
- `NEW_RUNTIME_VERIFY_FAILED_DISABLE_UNCONFIRMED` — אין אישור לחסימת LOGIN;
  לעצור, לא להעביר credentials ל־Render, ולברר ידנית בחיבור הבעלים.

NOLOGIN חוסם התחברויות חדשות בלבד; אין ניתוק sessions. הסיסמה החדשה צריכה
להישאר פרטית עד לסיום האימות. הכלי אינו מפעיל LOGIN מחדש אוטומטית אחרי כשל.

**אבחון בלבד**, מתוך עץ השחרור, עם DSN הבעלים הקיים:

```powershell
$runtimeProvisionPython = (Resolve-Path ..\..\.venv\Scripts\python.exe).Path
try {
    if ([string]::IsNullOrWhiteSpace($env:NEON_OWNER_DSN)) {
        $env:NEON_OWNER_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'Neon production / neondb_owner direct TLS DSN' -AsSecureString)).Password
    }
    & $runtimeProvisionPython -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --runtime-role viral_lab_demo_runtime --diagnose-runtime
    # Exit code 2 is expected if the old role is privileged; no changes were made.
} finally {
    Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue
    Remove-Variable runtimeProvisionPython -ErrorAction SilentlyContinue
}
```

**לפני ביצוע ידני מאושר**, אפשר להריץ אבחון קריאה בלבד.
`--diagnose-provisioning` מאמת תחילה את יעד Neon הישיר, הבעלים, TLS פעיל
וסכמה 0003 עם האילוצים וה־triggers הקיימים. הוא מדווח boolean על native
CREATEROLE והרשאות מנהל Neon בנפרד, grant options הנחוצים לכל הפקודות,
בעלות אפקטיבית הדרושה לביטול PUBLIC CREATE, recovery/default-read-only
וקיום התפקיד החדש. הוא אינו מבקש סיסמת runtime, אינו מייצר SCRAM, אינו
מבצע DDL/GRANT/REVOKE/probes ואינו יכול לשחזר כשל COMMIT בלי שינוי.
הפלט כולל רק בדיקות boolean וקודים קבועים, ללא ערכי שרת גולמיים.

מתוך `storage/submission-release`, באמצעות DSN הבעלים הקיים:

```powershell
$runtimeProvisionPython = (Resolve-Path ..\..\.venv\Scripts\python.exe).Path
try {
    if ([string]::IsNullOrWhiteSpace($env:NEON_OWNER_DSN)) {
        throw 'NEON_OWNER_DSN must already contain the existing direct TLS owner DSN.'
    }
    & $runtimeProvisionPython -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --runtime-role viral_lab_app_runtime --diagnose-provisioning
    if ($LASTEXITCODE -ne 0) { throw 'STOP: read-only provisioning diagnostic failed.' }
} finally {
    Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue
    Remove-Variable runtimeProvisionPython -ErrorAction SilentlyContinue
}
```

באבחון תקין מוחזר exit code 0 ו־`creation_uses_prehashed_password=false`.
זה אינו מבצע CREATE או מבטיח שסנכרון Neon ב־COMMIT יצליח. הרשאת שינוי
הגדרות logging נבדקת רק בעת ניסיון היצירה המפורש. אין טעינת `.env` או
fallback למסד מקומי. exit code 2 מחייב עצירה ובירור הקודים בלבד.

**רק לאחר בדיקת היעדר תפקיד ואישור נפרד לביצוע ולסיכון רישום הסיסמה
בצד השרת**, מתוך עץ השחרור, יצירה חד־פעמית במצב SQL של Neon
של התפקיד החדש, שבע ההרשאות ואימות דרך התחברות חדשה. תחילה יש ליצור
סיסמה אקראית ולשמור במנהל סיסמאות; Python יבקש אותה פעמיים בהסתרה.
אין להכניס אותה למשתנה סביבה, argv או פקודת PowerShell.

```powershell
$runtimeProvisionPython = (Resolve-Path ..\..\.venv\Scripts\python.exe).Path
try {
    if ([string]::IsNullOrWhiteSpace($env:NEON_OWNER_DSN)) {
        $env:NEON_OWNER_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'Neon production / neondb_owner direct TLS DSN' -AsSecureString)).Password
    }
    & $runtimeProvisionPython -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --runtime-role viral_lab_app_runtime --diagnose-provisioning
    if ($LASTEXITCODE -ne 0) { throw 'STOP: read-only provisioning diagnostic failed.' }
    & $runtimeProvisionPython -m backend.tools.cloud_runtime_permissions --confirm-synthetic-database viral_lab_submission_synthetic --runtime-role viral_lab_app_runtime --create-runtime --acknowledge-server-password-logging-risk
    if ($LASTEXITCODE -ne 0) { throw 'STOP: creation or verification was not confirmed. Inspect the fixed code; do not retry automatically.' }
} finally {
    Remove-Item Env:NEON_OWNER_DSN -ErrorAction SilentlyContinue
    Remove-Variable runtimeProvisionPython -ErrorAction SilentlyContinue
}
```

לאחר אישור, בבדיקה חוזרת של תפקיד קיים אפשר להשתמש
ב־`--verify-runtime`: תתבקש סיסמה בהסתרה, ייפתח חיבור runtime אמיתי
וכל probes הכתיבה יבוטלו. מצב זה לא משנה LOGIN, סיסמה או ACL של תפקיד קיים.
`--check --runtime-role viral_lab_app_runtime` נשאר בדיקת הרשאות לקריאה בלבד
מחיבור הבעלים, ואינו תחליף לבדיקת התחברות בפועל. אין לבצע אף בדיקה חיה
כזו לפני אישור. אין שימוש במסד `viral_lab` המקומי או ב־Gemini.

לאחר אימות חי מוצלח בלבד, אפשר להכין ל־Render secret של runtime עם אותו
host/database/TLS ושם המשתמש החדש והסיסמה השמורה, תוך URL-encoding תקין
של הסיסמה. לא להדפיס את DSN ולא להשתמש ב־DSN הבעלים בשירות. זה אינו אישור
לשינוי Render או לפריסה. הכלי והבדיקות הוכנו מקומית בלבד; ביצוע Neon ממתין לאישור.

Render Free אינו מספק shell או one-off jobs לפי
[המגבלות המתועדות](https://render.com/docs/free). לכן המיגרציה היא פקודה
מפורשת מהמחשב המורשה, מתוך עץ השחרור, לאחר אישור פריסה/שינוי Neon בלבד.
הפעילו venv עם dependencies הפרויקט. PowerShell, בלי `.env` המקומי:

```powershell
$env:DEMO_DATA_POLICY = 'synthetic-only'
$env:DEMO_DATABASE_NAME = 'viral_lab_submission_synthetic'
$env:REVIEW_DATABASE_DSN = [System.Net.NetworkCredential]::new('', (Read-Host 'New Neon migration-role direct TLS DSN' -AsSecureString)).Password
python -m backend.tools.cloud_migrate --confirm-synthetic-database viral_lab_submission_synthetic
python -m backend.tools.cloud_migrate --check
Remove-Item Env:REVIEW_DATABASE_DSN
```

הפקודה דורשת שם מסד מאושר, מאמתת host/TLS וזהות מסד/schema, ואז משתמשת
ב־Alembic connection המפורש ל־0001→0002→0003. היא אינה טוענת קובץ `.env`.
אין downgrade אוטומטי. בכשל מודפס קוד קבוע בלבד, ללא DSN או פרטי SQL.
לאחר הענקת הרשאות runtime יש לבדוק `--check` גם עם ה־DSN של runtime
ולשמור רק אותו ב־Render. DSN של בעל המסד אינו צריך להישאר בשירות.

ה־readiness וגם כל פקודת עסק POST מאמתים את `0003_form_approval`, CHECKs
קריטיים, PK/FK/UNIQUE ואת נוכחות/מצב/גוף ה־triggers הקפואים של נתונים
ו־append-only audit. חוסר או שינוי חוסמים כתיבה עם 503. אין migration בזמן
worker startup. בדיקות האינטגרציה משנות אילוצים רק ב־schemas חד־פעמיים.

## החסם הסינתטי — עדיין קיים במפורש

`backend.tools.seed_synthetic_demo` עדיין מסרב לכתיבת replay סינתטי ל־durable
עם `SYNTHETIC_DURABLE_PROVENANCE_UNSUPPORTED`. לא שינינו את live/live binding,
לא שתלנו snapshots מלאכותיים ולא יצרנו audit או אישור אנושי מראש.
גם `local_ocr` עדיין אינו מימוש חילוץ. תצורת הענן משתמשת כעת במתאם Gemini
הקיים, בכפוף להגדרות הספק, התחברות והסכמה; אין fallback לנתונים מדומים.

רשומות המבוססות על מסמכים סינתטיים יכולים להיווצר באופן ישר דרך **חילוץ חי
אמיתי**, שאושר במשימה זו, לאחר הסכמה מפורשת לכל קובץ וללא רשימת SHA קבועה.
זהו LIVE של פעולת החילוץ, לא טענה שהמטופל אמיתי. הבודק
נדרש לבדוק ולסיים כל טופס בעצמו. אם רוצים רשומות מאושרות לפני הגעת השופטים,
אדם מורשה צריך לבצע את הביקורת וההצהרה דרך API/UI, ולא seeder הממציא אישור.

אפשרות אחרת היא משימת המשך נפרדת לתמיכת provenance סינתטי לאורך storage,
codec, API ו־UI, עם בדיקות תאימות והערכה. היא לא מומשה כאן. בדיקות Mock
והדפדפן של משימה זו מסומנות tests בלבד ואינן factory מותר לפרסום.
אין להפעיל `tests.cloud_browser_server` ב־Render או להחליף אליו את startCommand.

## צ'קליסט ראשון ל־Render/Neon

1. לסקור את הקבצים ואת תוצאות הבדיקות. לאשר commit ו־push בנפרד; רק אז
   לפרסם את ענף השחרור ל־GitHub. לוודא שהקוד של אותו commit נגיש לשופטים.
   אין לפרסם `.env`, מסמכים, גיבויים, private keys או תיקיית storage.
2. חילוץ Gemini אמיתי אושר למסמכים סינתטיים בלבד. להכין PDF/PNG/JPEG
   ללא פרטי מטופלים אמיתיים; אין צורך לחשב או להגדיר hashes מראש.
3. ב־Neon Free: ליצור project/branch/database סינתטי חדש, ולבחור אזור קרוב
   ל־Frankfurt. להכין direct TLS DSN לתפקיד migration; לא להשתמש במסד המקומי.
4. אחרי אישור שינוי Neon, להריץ מקומית את פקודת המיגרציה שלמעלה. ליצור
   runtime role מוגבל, להעניק את ההרשאות ולהפיק עבורו DSN נפרד. לשמור
   credentials במנהל סיסמאות, לא ב־GitHub issues או בצ'אט.
5. מתוך עץ השחרור להריץ `python -m backend.tools.demo_password`; לשמור
   את ה־hash לשרת ואת הסיסמה להזמנה פרטית של השופטים. לא לכתוב סיסמה ב־VITE.
6. ב־Render Dashboard: New → Blueprint, לבחור את GitHub repo ואת ענף
   השחרור ואת `render.yaml`. פעולה זו יוצרת/פורסת שירות ולכן דורשת אישור
   deployment קודם. לבדוק שמוצע שירות Web אחד Free בלבד וללא Render DB.
7. למלא `REVIEW_DATABASE_DSN` של runtime ו־`DEMO_PASSWORD_HASH` בשדות הסודיים.
   להוסיף `GEMINI_API_KEY` כסוד ואת ארבעת דגלי Gemini המפורטים למעלה.
   להגדיר `GEMINI_MODEL=gemini-3.8-flash` ולא להעתיק `.env` עסקי. אם בוחרים יצירה ידנית במקום
   Blueprint, להעתיק במדויק root/build/start/health/env המפורטים למעלה.
8. לבצע deploy ידני מאושר. לבדוק שה־build מתקין Node/Python ושה־service
   עולה על PORT שהוקצה. `/api/v1/ready` צריך להיות 200; אם 503, לבדוק
   מיגרציה/הרשאות/TLS דרך `cloud_migrate --check`, ולא להסיר את השער.
9. לפתוח את כתובת HTTPS החינמית של **אותו Web Service**, עם Third-Party
   Cookies חסומות. לבדוק כניסה/יציאה, שגיאת סיסמה, source/export אנונימיים
   שנדחים, ו־/docs ו־/openapi.json שאינם זמינים. לשתף סיסמה בערוץ פרטי.
10. לאחר החלת הגדרות Gemini ופריסה מאושרת: לבצע התחברות, הסכמה והעלאת
    כמה קבצים סינתטיים נפרדים בתור אחד, ללא הגדרת hashes, פתרון חריגים,
    אישור טופס מפורש, ייצוא משותף ושורה לכל התחייבות. לבדוק ש־429/503/504
    מוצגים ככשל ולא כהצלחה. לא להשתמש במסמכים רפואיים אמיתיים.
11. לבצע restart מאושר לשירות, להתחבר שוב ולפתוח רשומה שמורה מ־Neon.
    אין להפעיל חילוץ מחדש כדי לפתוח רשומה. לאמת quotas, grants, לוגים והיעדר
    סודות ב־bundle. אין durable jobs: עיבוד שנקטע עלול להישאר processing.
12. למסור לשופטים את כתובת ה־Web Service ואת קישור ה־GitHub ל־commit/branch
    שנבדק, עם אזהרת cold start והוראות כניסה קצרות. לא למסור credentials
    למסד או מפתח Gemini. אחר ההאקתון לסגור גישה ולטפל בשמירת הנתונים.

## בדיקות ומגבלות

בתיקון הפעלת Gemini בענן הורצו מחדש: `pytest -q -p no:cacheprovider
--ignore=tests/integration` — **1,178 עברו**, כולל **50 בדיקות ענן**;
`npm --prefix frontend test` — **54 עברו**; typecheck ו־production build עם
`VITE_PUBLIC_DEMO=true` עברו. אין כשלי בדיקות. נבדקו שלושה סוגי קבצים שונים
דרך המתאם האמיתי עם תעבורת ספק מדומה, ביקורת, אישור ידני, פתיחה חוזרת
ו־XLSX עם שש שורות לשלושה מסמכים. נבדקו העדר הסכמה/auth/CSRF, מגבלת
5 MiB, שערי מדיניות, rate limits ו־429/503/504 ללא fallback או ניסיון חוזר.
לא הורצו בדיקות PostgreSQL מחדש, לא בוצעה קריאת Gemini חיה ולא נמדדה איכות
חילוץ. התוצאות להלן שייכות לאימותים קודמים ואינן אימות הפריסה החדשה בענן.

- Backend offline: **953 עברו, 0 נכשלו, 0 דולגו** בהרצה מלאה של
  `pytest -q -p no:cacheprovider --ignore=tests/integration` (117.13 שניות).
  לאחר הקשחת טיפול בכותרות חריגות נוספה בדיקה אחת, וכל **37 בדיקות הענן**
  ב־`tests/test_cloud_demo.py` עברו שוב (38.93 שניות). 36 מהן נכללו בריצה
  המלאה; אין לסכום את שני המספרים כאילו היו בדיקות שונות.
- PostgreSQL: **77 עברו, 0 נכשלו, 0 דולגו** ב־181.89 שניות, כולל תשע
  בדיקות cloud מול repository אמיתי. רק `review_test_viral_lab` המקומי,
  schemas זמניים ומלאי schemas/טבלאות זהה לפני ואחרי. נבדקו PK/FK/UNIQUE,
  CHECK, trigger body, מיגרציה, admission quota, אישור ופתיחה אחרי יישום חדש.
  לא בוצעו restart של PostgreSQL עצמו או חיבור למסד עסקי/Neon.
- Frontend: **53 עברו, 0 נכשלו, 0 דולגו**. TypeScript strict typecheck עבר.
- Production build עם `VITE_PUBLIC_DEMO=true` עבר: 32 modules, JS בגודל
  290.97 kB ו־CSS בגודל 27.92 kB לפני gzip. esbuild נדרש להרשאת סביבת
  הרצה; אותה פקודת build עברה ללא שינוי כלי הבנייה.
- `frontend/tests/cloud-browser-smoke.ts` עבר מול HTTPS מקומי, React הבנוי
  ו־ASGI אמיתי. Third-Party Cookies נחסמו ב־Chromium, והתחברות first-party,
  cookie HttpOnly/Secure/Strict, דחיית anonymous/CSRF, העלאה, XLSX ו־logout
  פעלו. ספק ואחסון מדומים ומסומנים; אין PostgreSQL או Gemini בבדיקה זו.
  תעודה עצמית ו־certificate exception שימשו רק בפרופיל הבדיקה הזמני,
  ואינם חלק מפקודת Render או מהגדרות production.
- גם `frontend/tests/form-approval-e2e.ts` עבר: React→FastAPI→Excel,
  שני קבצים נוספים, bulk export ואירוע אישור אחד לטופס, ללא ספק חי או מסד.
- `pip check`, Python AST, בדיקת bundle ללא סיסמת בדיקה/DSN/API key,
  `git diff --check` ובדיקת whitespace לקבצים חדשים עברו. אין בפרויקט linter,
  formatter או Python typechecker מוגדרים; לא נטען שהורצו. אין benchmark AI.
- `render.yaml` נקרא כ־YAML ונבדקו מקומית service אחד, Free, auto-deploy off,
  PORT, worker אחד, היעדר migration אוטומטי והיעדר סודות במשתני VITE.
  אין זו בדיקת Blueprint דרך Render API או הוכחת build בפלטפורמה.

רשימת השינויים המדויקת של משימה זו ו־Git status מלא נמצאים
ב־[cloud-changes.json](cloud-changes.json). הקבצים הקודמים נשמרו גם בגיבוי
מקור מקומי מוחרג לפני העריכה. לא הוסרה אף יחידת מקור קיימת, ולא שונו
היסטוריית Git, עץ המקור או עץ הפרימיום. source-files.json מתייחס כעת
למועמד הענן; דוחות/ארכיון המועמד הקודם נשמרים כתיעוד היסטורי.

קבוצות הקבצים במשימה: `backend/cloud_app.py`, `backend/core/cloud_config.py`,
`backend/core/demo_auth.py`, `backend/persistence/cloud_database.py`,
`backend/persistence/business_repository.py`, כלי `cloud_migrate`/`demo_password`,
`render.yaml`, קובצי env.example, עטיפת `AuthGate` וחיבור ה־API/main/App/CSS,
בדיקות cloud ל־Python/PostgreSQL/Frontend/Chromium, README ודוחות השחרור.
רשימת paths ו־hashes ב־manifest מבחינה בינם לבין שינויים שהיו קיימים לפני
המשימה; diff מול HEAD לבדו כולל גם את עבודת השילוב הקודמת.

מצב Git הסופי: **32 קבצים tracked ששונו, 56 קבצים חדשים, 0 staged**.
במשימה זו נוצרו/עודכנו **27 paths**, כולל שני manifests. סך הגשת המקור:
**197 קבצים**. קובץ מקור נקי חדש נמצא ב־
`storage/render-preparation/viral-lab-render-neon-2026-10-21-final.zip` בתוך
עץ השחרור. הוא כולל גם קבצים חדשים שלא בוצע להם staging, ללא build,
node_modules, SSL keys, מסמכים רפואיים או `.env`. הוא אינו כולל היסטוריית
Git; ההיסטוריה נשארת במאגר, והפרסום ל־GitHub צריך לעבור דרך commit מאושר.
הארכיונים הקודמים לא נדרסו. לא בוצעו commit, push או deployment.

הרצות מקומיות אינן אישור security מוחלט או הוכחת פריסה ציבורית: לא בוצעו
Linux install נקי, חיבור Neon, הגדרת role בפלטפורמה, Render health probe או
בדיקת URL חי. יש לבדוק אותם ברשימת הקבלה לאחר אישור deployment.
Render Free עשוי להירדם אחרי 15 דקות, להתעורר באיטיות ולאבד קבצים מקומיים;
הנתונים העסקיים נשארים ב־Neon. תורי העיבוד אינם durable jobs, sessions
ומגבלות קצב בזיכרון מתאפסים ב־restart, ו־audit עדיין עשוי לצרוך את מכסת Neon.
אין claim של דיוק קליני, שינוי prompt או benchmark חי במשימה זו.

מקורות התצורה: [Render Web Services](https://render.com/docs/web-services),
[Blueprint reference](https://render.com/docs/blueprint-spec),
[Python runtime](https://render.com/docs/python-version),
[Neon connection guidance](https://neon.com/docs/get-started/connect-neon).

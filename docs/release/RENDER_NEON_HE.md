# הדגמת מוזמנים ב־Render + Neon — 21.10.2026

זהו מימוש מקומי ותכנון פריסה שטרם הועלה לענן. אין קישור demo חי שנוצר
במשימה זו: המשתמש אסר commit, push ו־deploy. אין חיבור ל־Neon או למסד
העסקי המקומי, ואין קריאת Gemini. הקוד נמצא רק ב־`storage/submission-release`,
בענף `release/submission-2026-10-22`. HEAD נשאר `01315c9733ae8f9310275fc9d8921de510dae5b7`.

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
  עיבוד העלאה אחד בכל רגע; מתחרה מקבל 429. אין retry סמוי של provider.
- Neon: עד 30 מסמכים ו־32 MiB של bytes מקור, גם בכשלים. admission ננעל
  ב־PostgreSQL advisory transaction lock ונבדק באותה עסקה של INSERT.
  snapshot לענן מוגבל ל־64 KiB; התקרה הקיימת של 32 טפסים נשמרת.
  היסטוריית הביקורת אינה נמחקת אוטומטית; צריך לעקוב אחר מכסת Neon.
- קודי provider אמיתיים נשמרים: rate limit → 429, unavailable → 503,
  timeout → 504. גוף השגיאה שומר run_id וכשל שמור, ואינו ממציא רשומה מוצלחת.
  ה־Frontend שומר את capability של ריצת הכשל גם בתשובת HTTP שגויה, עוצר את
  התור ללא retry ומאפשר דוח כשל/ייצוא כשל משותף, בלי להציג טופס מאושר.
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
  זהו ה־origin שהוגדר מקומית; לא בוצע push ולא נבדקה נגישות הגרסה החדשה
  לשופטים. יש לוודא שהריפו/ה־commit נגישים להם לאחר הפרסום המאושר.
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
- `AI_OUTBOUND_ENABLED=false`, `ALLOW_EXTERNAL_PHI=false`,
  `APPROVED_AI_PROVIDERS=` ו־`DEFAULT_EXTRACTION_ADAPTER=local_ocr`.
- הגדרות build הציבוריות שפורטו למעלה.

המשתמש מוסיף ב־Render Environment, בלי להכניס לריפו:

- `REVIEW_DATABASE_DSN`: חיבור **direct/unpooled** של Neon למסד החדש,
  עם `sslmode=require&channel_binding=require`. מתקבל גם scheme
  `postgresql://`/`postgres://`, שמומר ל־`postgresql+psycopg` בלי לאבד TLS.
  host חייב להסתיים ב־`.neon.tech`; `-pooler`, loopback, מסד אחר ופרמטרי
  override של host/dbname נדחים לפני חיבור. בריכת האפליקציה מוגבלת לשני
  חיבורים, connect timeout עשר שניות, statement timeout 15 שניות ו־lock
  timeout חמש שניות. אין חיבור מתוך הפקודה הזו ל־`viral_lab` המקומי.
- `DEMO_PASSWORD_HASH`: הפלט של מחולל הסיסמה ב־Backend. לא הסיסמה הגלויה.
- `DEMO_ALLOWED_DOCUMENT_SHA256`: רשימת SHA-256 של עד 30 קובצי PDF/PNG/JPEG
  סינתטיים שנבדקו ידנית ואושרו. ערך ריק חוסם כל העלאה. שם קובץ או הצהרת
  checkbox אינם מחליפים allowlist. אין hash של מסמך אמיתי ברשימה.
- `GEMINI_API_KEY` ו־`GEMINI_MODEL`: **רק בעתיד ובהרשאה נפרדת**, Backend
  בלבד. עצם הימצאות מפתח אינה מתירה יציאה או מחליפה מדיניות ספק.

## Neon ומיגרציה מפורשת

ב־Neon Console יש ליצור project/branch/database חדשים עם נתונים סינתטיים
בלבד, לא clone/restore של מסד רפואי. בחרו את אותו אזור של Render ככל האפשר.
שם המסד הנוכחי בקונפיגורציה: `viral_lab_submission_synthetic`.
ב־Connect בחרו direct connection (כבו connection pooling), role ו־database
הנכונים, והעתיקו את מחרוזת TLS לשדה הסודי המתאים בלבד.

מומלץ role בעל DDL למיגרציה ו־role runtime נפרד ללא בעלות/הרשאות DDL.
Neon Console/SQL משמשים ליצירת התפקידים; אין ליצור סיסמה בתוך קובץ מקור.
לאחר שה־role הלא־privileged בשם `viral_lab_demo_runtime` קיים, בעל המסד יכול
להעניק לו במסד החדש בלבד:

```sql
GRANT CONNECT ON DATABASE viral_lab_submission_synthetic TO viral_lab_demo_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO viral_lab_demo_runtime;
GRANT SELECT ON alembic_version TO viral_lab_demo_runtime;
GRANT SELECT, INSERT ON business_documents, business_records, business_review_events TO viral_lab_demo_runtime;
GRANT UPDATE (status, extraction_id, canonical_snapshot_json, canonical_snapshot_sha256,
              failure_json, failure_sha256) ON business_documents TO viral_lab_demo_runtime;
GRANT UPDATE (revision, review_status, projection_json, projection_sha256)
  ON business_records TO viral_lab_demo_runtime;
```

אין להעניק owner/superuser/neon_superuser, DELETE או schema CREATE ל־runtime.
בדיקת הרשאות הענן עצמן טרם בוצעה; יש לאמת אותן מול המוצר לאחר פריסה.
אם Neon יוצר role עם הרשאות מוגברות דרך ה־Console, יש להסירן לפני שימוש
בו ביישום או ליצור role מוגבל במנגנון הנתמך של Neon.

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
גם `local_ocr` עדיין אינו מימוש חילוץ. לכן ברירת המחדל בטוחה אך אינה נותנת
הדגמת upload→extraction→review על Neon ריק. לא להציג מסך ריק כהדגמה מלאה.

רשומות המבוססות על מסמכים סינתטיים יכולים להיווצר באופן ישר דרך **חילוץ חי
אמיתי**, לאחר אישור נפרד של העיבוד והספק, כשה־SHA-256 של הקבצים הסינתטיים
ברשימת ההרשאה. זהו LIVE של פעולת החילוץ, לא טענה שהמטופל אמיתי. הבודק
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
2. לבחור כיצד ייפתר חסם הנתונים: אישור חילוץ חי של **סינתטי מאושר בלבד**,
   או משימת provenance נפרדת. בלי החלטה זו אפשר להדגים התחברות ו־health,
   אך אי אפשר להבטיח תהליך עסקי מלא על מסד ריק.
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
   לוודא שכל משתני ברירת המחדל של Blueprint קיימים. לא לשנות AI flags
   ללא אישור נפרד, ולא להעתיק `.env` עסקי. אם בוחרים יצירה ידנית במקום
   Blueprint, להעתיק במדויק root/build/start/health/env המפורטים למעלה.
8. לבצע deploy ידני מאושר. לבדוק שה־build מתקין Node/Python ושה־service
   עולה על PORT שהוקצה. `/api/v1/ready` צריך להיות 200; אם 503, לבדוק
   מיגרציה/הרשאות/TLS דרך `cloud_migrate --check`, ולא להסיר את השער.
9. לפתוח את כתובת HTTPS החינמית של **אותו Web Service**, עם Third-Party
   Cookies חסומות. לבדוק כניסה/יציאה, שגיאת סיסמה, source/export אנונימיים
   שנדחים, ו־/docs ו־/openapi.json שאינם זמינים. לשתף סיסמה בערוץ פרטי.
10. רק אם נפתר חסם הנתונים והספק אושר: לקבוע hashes לקבצים הסינתטיים
    שנבדקו, לעדכן Backend env, ולבצע העלאת שני קבצים נפרדים, פתרון חריגים,
    אישור טופס מפורש, ייצוא משותף ושורה לכל התחייבות. לבדוק ש־429/503/504
    מוצגים ככשל ולא כהצלחה. לא להשתמש במסמכים רפואיים אמיתיים.
11. לבצע restart מאושר לשירות, להתחבר שוב ולפתוח רשומה שמורה מ־Neon.
    אין להפעיל חילוץ מחדש כדי לפתוח רשומה. לאמת quotas, grants, לוגים והיעדר
    סודות ב־bundle. אין durable jobs: עיבוד שנקטע עלול להישאר processing.
12. למסור לשופטים את כתובת ה־Web Service ואת קישור ה־GitHub ל־commit/branch
    שנבדק, עם אזהרת cold start והוראות כניסה קצרות. לא למסור credentials
    למסד או מפתח Gemini. אחר ההאקתון לסגור גישה ולטפל בשמירת הנתונים.

## בדיקות ומגבלות

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

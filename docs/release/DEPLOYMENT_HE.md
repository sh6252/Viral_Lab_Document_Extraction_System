# פריסה בטוחה של מועמד השחרור — 21.10.2026

> מסמך היסטורי של מועמד השחרור המקורי. המשתמש אישר בהמשך Render + Neon,
> ולאחר בדיקת Cookies בחרנו בשירות Render יחיד עם React ו־API באותו origin.
> למצב הנוכחי ולהוראות המעודכנות ראו [RENDER_NEON_HE.md](RENDER_NEON_HE.md).

זהו נוהל מוצע לביצוע לאחר אישור נפרד. לא בוצעו פריסה, push, commit,
שינוי במסד העסקי או קריאת Gemini. היעד: הדגמה פרטית עם מידע סינתטי בלבד
עד ערב 22.10.2026; אין כאן אישור לשימוש במידע רפואי אמיתי.

## מצב שנמצא בקוד

- נקודת הכניסה הכללית היא `backend.main:app`. היא כוללת health, extraction
  וממשקי review המוגנים בהזרקת authenticator/policy. היא **אינה** מפעילה את
  מסכי הרשומות, העלאת ה־demo והייצוא שהממשק הנוכחי צריך.
- המסלול העמיד של המוצר מופעל באמצעות
  `backend.tools.durable_demo:create_durable_demo_app --factory`. הוא דורש
  `REVIEW_DATABASE_DSN`, בודק זמינות טבלאות ונכשל ללא fallback לזיכרון.
  אינו מריץ migration בהפעלה ואינו טוען `.env` אוטומטית.
- `LocalDemoRoute` בודק peer מקומי, Host מקומי, Origin מדויק מתוך localhost/
  127.0.0.1 בפורטים 5173/8000, כותרת `X-Local-Demo: 1` וחסימת cross-site.
  אין CORS middleware מתירני. החלפת bind ל־0.0.0.0 אינה פתרון לפריסה.
- כותרת הדמו, הצהרת הבודק ו־review_token אינם התחברות של משתמש. רשימת
  הרשומות ופתיחת רשומה מחזירות גישה במסגרת גבול ה־loopback ללא ACL למשתמש.
  `/api/v1/extractions` הכללי אינו דורש authentication. פרסום השרת כולו
  יחשוף גם מסלול זה ואת `/docs`; אין להניח ששערי demo מגינים על כל היישום.
- ה־frontend משתמש בנתיבי `/api/v1/...` יחסיים וב־`credentials: omit`.
  אין כרגע משתנה `VITE_API_URL`. ה־proxy ב־`frontend/vite.config.ts` מיועד
  לפיתוח בלבד אל 127.0.0.1:8000; build אינו כולל שרת proxy.
- מקור המסמך נשמר ב־PostgreSQL כ־bytes עם hash; אין static upload directory.
  מסלול `/source` מוגן בשער המקומי ומחזיר no-store. tokens, מסמך, חיפושים
  ותגובות API חייבים להישאר מחוץ ללוגים. אין הרשאות לפי משתמש או מדיניות
  retention מלאה למסלול העסקי הנוכחי.
- המיגרציות: `0001_review_persistence` → `0002_business_persistence` →
  `0003_form_approval`. השלישית משנה CHECK לאירוע `__form__`; אין downgrade
  אוטומטי בטוח לאחר אישור טופס. `check_ready()` בודק טבלאות, לא תחליף לאימות
  `alembic_version` ו־CHECK לפני קבלת כתיבות.
- `GET /api/v1/health` הוא liveness בלבד: אינו פונה למסד או לספק. נדרשת
  בדיקת readiness נפרדת של PostgreSQL והמיגרציה. זה אינו endpoint קיים נוסף.
- קיימים pyproject, package-lock, Alembic ומשגרי Python. לא נמצאו Dockerfile,
  Compose, nginx config, systemd unit, Procfile או תצורת hosting/CI מוכנה.
  Docker מותקן במחשב הפיתוח; זו אינה תצורת deployment של המאגר.
- אין worker או durable jobs פעילים. העיבוד סינכרוני; מסמך שנקטע במצב
  processing אינו מקבל התאוששות אוטומטית. persistence עסקי אינו durable queue.

## חסמים קריטיים

1. **לשירות ציבורי:** חסרים identity מאומת, הרשאות למשאבי מסמך/טופס/ייצוא,
   מודל משתמשים ומדיניות session/CSRF. אין לחשוף את המשגר המקומי לאינטרנט,
   ואין להסיר את בדיקות ה־loopback כדי להתאים ל־PaaS.
2. **להדגמה מלאה ללא AI:** `local_ocr` אינו ממומש. לא קיים משגר durable
   סינתטי עם מקור נתונים המסומן נכון. `DemoWorkflow` ו־BusinessRepository
   דורשים מקור live/live; seeder מסרב לכתיבה עם
   `SYNTHETIC_DURABLE_PROVENANCE_UNSUPPORTED`. אין להזריק mock ולכנותו LIVE.
   בדיקת ה־E2E ושרת ה־preview הן בדיקות סינתטיות, אינן פתרון deployment.
3. **לפני כתיבות בענן:** צריך מסד חדש וסינתטי בלבד, סודות חדשים ומיגרציות
   0001–0003 מאומתות. המסד המקומי `viral_lab`, הגיבויים ו־`.env` הנוכחי אינם
   חומר לפריסה. במשימה זו נבדק רק `review_test_viral_lab`.
4. **לכתובת ציבורית נגישה לשופטים:** gateway מאומת עם HTTPS הוא עבודה נוספת
   שצריכה בדיקות Host/Origin/auth, הגבלת גישה ולוגים. אין במאגר תצורה מאומתת
   כזו; reverse proxy לבדו אינו authentication.

## מגבלות נוספות שאינן חוסמות הגשת קוד מקומית

- ללא Docker/CI, ניטור readiness, גיבוי ושחזור בענן או בדיקת Linux נקייה.
- Python dependencies מוגדרות בטווחי גרסאות; אין lock נייד מלא. Node נעול
  ב־package-lock, והגרסה שנבדקה היא Node 24.14.0 / npm 11.4.2.
- אין בדיקת עומס או אכיפת מכסה כוללת מתמשכת למסד; יש גבולות לקובץ, לתוצאות,
  לתור הדפדפן ולייצוא. יש להגביל בקשות גם בגבול הפריסה.
- התור בדפדפן אינו משתחזר לאחר סגירה. PostgreSQL שנפתח מחדש נבדק ברמת
  חיבורים/יישום; לא בוצע restart לשירות PostgreSQL עצמו.
- לא נמדדה איכות חילוץ חיה או השוואת baseline/holdout של prompt. אין להסיק
  דיוק קליני מתוצאות בדיקות תוכנה. אין פיצול אוטומטי של PDF רב־עמודי.

## תצורת האחסון המומלצת לזמן שנותר

VM יחיד של Linux, התחלה סבירה של 2 vCPU / 4 GiB RAM לצורכי הדגמה בלבד
(אינה הערכת קיבולת), עם PostgreSQL מקומי בדיסק מתמשך, Uvicorn worker אחד
ו־nginx לקובצי `frontend/dist`. אין Redis, Celery, Kubernetes או microservices.
כל שירותי HTTP והמסד מאזינים ל־127.0.0.1 בלבד; הגישה דרך SSH עם מפתח
ורשימת כתובות מותרות. אין לפתוח 8000, 5173, 5432 או 80/443 לאינטרנט.
כך אפשר לשמור את גבול ה־loopback הקיים. עבור לינק ציבורי נדרש פרויקט auth
נפרד; תחת מועד ההגשה עדיפה הדגמה פרטית או מקומית על חשיפה לא מוגנת.

nginx ישרת את ה־build על 127.0.0.1:5173 ויעביר `/api/` ל־127.0.0.1:8000,
עם Host קבוע `127.0.0.1:8000`, ללא cache של API או access log של פרטי מסמכים.
Origin הדפדפן נשאר `http://127.0.0.1:5173`. אין לשנות את allowlist בקוד.
יש להגביל גוף בקשה מעל מעטפת multipart של 20 MiB ולהגדיר timeout מתאים.
הקונפיגורציה הזו היא המלצה שטרם הותקנה או נבדקה, לא קובץ deployment מוכן.

המלצה זו נשענת על [נקודת כניסה ASGI ב־FastAPI](https://fastapi.tiangolo.com/deployment/manually/),
[הנחיות Vite לשרת build סטטי](https://vite.dev/guide/static-deploy.html),
[חיבורי PostgreSQL מקומיים](https://www.postgresql.org/docs/16/runtime-config-connection.html)
ו־[local forwarding של OpenSSH](https://man.openbsd.org/ssh). אין להשתמש
ב־`vite dev` או `vite preview` כשרת production.

## צעדים מדויקים לאחר אישור נפרד

1. לאשר את קובצי מועמד השחרור ולבצע commit אטומי בענף השחרור. לבדוק את
   `source-files.json` וארכיון המקור; אין להעלות את תיקיית העבודה בשלמותה.
   שני עצי המקור נשארים עם השינויים שלהם. push רק באישור מפורש נוסף.
2. לבחור **הדגמה מקומית/פרטית** או לאשר משימת המשך מצומצמת שתפתור את חסם
   המקור הסינתטי. עד אז ניתן להציג בנפרד preview עם banner סינתטי ובדיקת
   E2E שיוצרת XLSX אמיתי; אין להציגם כמוצר durable בענן. אין להפעיל Gemini
   כמעקף לחסם. לא להעביר מסמכים אמיתיים או את המסד המקומי.
3. לאחר פתרון החסם, להקים VM ללא HTTP ציבורי; SSH במפתח בלבד, firewall לפי
   IP, משתמש שירות ללא root, דיסק מתמשך, PostgreSQL רק ב־loopback ו־SCRAM.
   להעתיק רק source ZIP מאומת או clone של commit מאושר. אין להעתיק `.env`.
4. ליצור venv Python 3.12/3.13, להתקין `.[dev]` לצורך אימות הראשוני,
   ולהריץ `pip check`. להריץ `npm ci`, בדיקות, typecheck ו־build. לבדוק
   מחדש בסביבת Linux; הרצת Windows הנוכחית אינה הוכחה לכך.
5. ליצור מסד חדש כגון `viral_lab_submission_synthetic`, עם תפקיד migration
   נפרד ותפקיד runtime בעל הרשאות מינימליות. לא לשנות את `viral_lab` הקיים.
   לשמור DSN בקובץ שירות מחוץ למאגר, למשל `/etc/viral-lab/release.env`,
   בהרשאות 0600 ובבעלות מתאימה. אפשר להשתמש ב־secret store של ספק הענן.
6. הגדרות בטוחות: `AI_OUTBOUND_ENABLED=false`, `ALLOW_EXTERNAL_PHI=false`,
   `APPROVED_AI_PROVIDERS=` ו־`DEFAULT_EXTRACTION_ADAPTER=local_ocr`;
   אין מפתח Gemini. הגדרות אלו מונעות חילוץ חדש; אינן מייצרות mock.
   משגר סינתטי עתידי חייב להיות נפרד, מפורש ומאומת לפני שינוי ההגדרות.
7. לאחר בדיקת שם המסד החדש וה־DSN, להריץ בו בלבד:
   `python -m alembic upgrade head`. לאמת `alembic_version=0003_form_approval`
   ואת `ck_business_event_field`, לבדוק שמירת רשומה סינתטית, גיבוי ושחזור.
   אין להפעיל migration על המסד העסקי מכוח נוהל זה.
8. להגדיר service manager עם EnvironmentFile מחוץ למאגר ולהפעיל ממשגר
   השחרור, ללא reload:
   `python -m uvicorn backend.tools.durable_demo:create_durable_demo_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --no-proxy-headers`.
   זהו משגר demo עמיד ופרטי, לא מימוש authentication יישומי.
9. להגדיר nginx מקומי כמפורט למעלה. מהמחשב המציג להתחבר עם
   `ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5173:127.0.0.1:5173 demo-user@demo-host`
   ולפתוח `http://127.0.0.1:5173`. מפתח SSH הוא גבול הגישה של ההדגמה הפרטית;
   סיסמת בודק בטופס והכותרת המקומית אינן תחליף לו.
10. לבדוק health, חיבור למסד, revision ו־CHECK. רק לאחר פתרון חסם המקור:
    שני קבצים סינתטיים נפרדים → חריגים → אישור טופס יחיד → ייצוא משותף →
    שורה לכל התחייבות → restart ליישום → פתיחה חוזרת. לוודא אפס AI outbound,
    אפס אישורים מדומים וחסימת גישה ללא SSH. כל כשל משאיר את ההדגמה פרטית.

פריסה ציבורית עם TLS, gateway מאומת והרשאות יישומיות אינה כלולה במועמד זה.

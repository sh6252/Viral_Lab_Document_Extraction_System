# אישור אנושי כולל לטופס — ללא קבלה אוטומטית

המימוש תואם לחלופה שאושרה במפורש: טיפול בחריגים ואחריו הצהרה אנושית אחת
על **כל ערכי הטופס המוצגים**, בהשוואה למקור. שדה מלא ללא דגל אינו ראיה
לדיוק. אין קבלה אוטומטית, ציוני ביטחון חדשים, OCR או שינוי בפרומפט.

## בידוד ושינויים

- שרת: העותק המקורי, `feat/durable-business-records`, כולל תיקוני Excel
  והפרומפט הקיימים. לא הועתק backend לעותק העיצוב.
- React: רק `storage/premium-ui-worktree`, בענף `feat/premium-ui-redesign`.
- שינויים קיימים נשמרו; לא בוצעו commit, push, merge או מיגרציה במסד קיים.
- קובצי מקור לפני השינוי גובו מקומית בתיקייה המתעלמת מ־Git:
  `storage/premium-ui-worktree/frontend/storage/form-finalization-before`.
  הגיבוי אינו כולל סודות או מסמכי מקור.

קובצי המשימה בעותק המקורי:

- `backend/services/business_review.py`, `backend/services/form_review_policy.py`,
  `backend/services/demo_workflow.py`, `backend/services/durable_workflow.py`,
  `backend/services/excel_export.py`.
- `backend/api/demo_schemas.py`, `backend/api/routes/demo.py`.
- `backend/persistence/business_codec.py`, `backend/persistence/business_models.py`,
  `backend/persistence/business_repository.py`, `backend/persistence/business_search.py`.
- `migrations/versions/0003_form_approval.py`, `docs/form_approval.md`.
- `tests/test_form_approval.py`, `tests/integration/test_form_approval.py`,
  `tests/fixtures/business_review_v1.json`, `tests/form_approval_bridge.py`.

קובצי המשימה בתוך `storage/premium-ui-worktree/frontend`:

- `src/App.tsx`, `src/api.ts`, `src/workbench.ts`, `src/style.css`.
- `tests/appHarness.ts`, `tests/FormApproval.test.ts`, `tests/form-approval-e2e.ts`.
- `BATCH_UX_NOTES.md`, `DESIGN_NOTES.md`.

## המדיניות: human-form-attestation-v1

`form_review_policy.review_reasons` מחזיר קודי סיבה דטרמיניסטיים:
חסר/ריק ללא אישור ריק מפורש; דחייה או בירור שטרם נפתרו; מצב חילוץ שאינו
present; דגל needs_review/needs_clarification; הערה לא ריקה או signals לא ריקים;
ריבוי ערכים בשדה יחיד; תאריך שאינו תאריך מלא ותקין; נרמול מזהה שמשנה את
המחרוזת, או נרמול תאריך לא תקין/לא תואם. אין שימוש בסף confidence.

אישור שדה מפורש יכול לפתור דגל של הספק. תיקון עדיין חייב לעבור בדיקות ערך
וריבוי; אי אפשר לסיים עם תאריך מתוקן לא תקין. אישור שדה ריק מפורש מתיר ריק.
מזהים נשארים מחרוזות, כולל אפסים, פיסוק וריבוי ערכים. לא נוספו כללי אורך,
checksum או רשימת שולחים שלא הוגדרו בחוזה הקיים.

היעדר דגל אינו קבלה: גם הערכים האחרים מחייבים הצהרה אנושית כוללת.
אין בחוזה הנוכחי בסיס לקבלה אוטומטית בטוחה; `automatic_acceptance` הוא false.
כללי גרסה זו חייבים להישאר יציבים לצורך שחזור היסטוריה. שינוי עתידי דורש
גרסת מדיניות חדשה, שמירת השחזור הישן והערכת השפעה נפרדת.

## API, תיעוד ועסקה

`POST /api/v1/demo-runs/{run_id}/forms/{form_id}/finalize`

נדרשים review_token, expected_revision, decision_id, operator_declaration,
declared_at עם אזור זמן, ו־attest_all_values שהוא true ממש. הלקוח אינו שולח
ערכי מקור או רשימות אישורים. השרת משתמש ב־snapshot ובמצב השמור.

הצהרת `FormApprovalAction` אחת יוצרת אירוע `FormApprovalAuditEntry` אחד עם
field_name לוגי `__form__`, action=finalize_form וגרסה חדשה אחת. ערכים שלא
קיבלו החלטת שדה נשמרים כ־accepted_by_form עם מקור human_form_attestation.
הם אינם approved_provider או approved_corrected. תיקונים ואישורי ריק
קיימים נשארים כפי שהם. ה־snapshot המקורי וה־hash שלו אינם משתנים.

נעילת מסמך האב, בדיקת הגרסה, שמירת ההיטל והוספת האירוע מתבצעות בעסקת
PostgreSQL קיימת אחת. שליחה חוזרת זהה אינה מוסיפה אירוע; decision_id חוזר
עם תוכן אחר נדחה. אישור חדש לטופס שכבר סוים נדחה. תיקון מאוחר יותר שומר
את ההיסטוריה ומבטל את סימון הסיום הנוכחי; נדרשת הצהרה חדשה.

תיקון רגיל יכול להשמיט reason. במקרה זה נשמר הקוד
`SYSTEM:ROUTINE_CORRECTION_NO_FREE_TEXT`, ולא נימוק שמתחזה לטקסט של המשתמש.
יתר פעולות השדה ממשיכות לדרוש נימוק. ההצהרה היא עצמית, לא אימות זהות.
כל שערי loopback, Origin, הכותרת המקומית, פרטיות המקור והסכמת הספק נשמרו.

## אחסון ומיגרציה — טרם הופעלה

`0003_form_approval` מרחיבה רק את CHECK של business_review_events.field_name
כדי להתיר __form__. אינה משנה רשומות, snapshots, hashes, מפתחות או triggers
המונעים שינוי של ההיסטוריה. לא נדרשות טבלאות חדשות. downgrade מסרב לפעולה
ללא תוכנית ארכוב. אין להריץ על המסד הקיים כחלק מבדיקת המימוש.

רשומות ישנות ממשיכות לפענח ולהתקודד ל־business-storage-v1 בדיוק לאותם bytes.
תוכן עם אישור טופס משתמש ב־business-storage-v2-form-approval. נבדק מול fixture
סינתטי שנוצר בקוד הישן לפני העריכה. רשומה ישנה שהושלמה בשבע החלטות נשארת
complete; היא אינה מקבלת אירוע סיום חדש מעצמה.

עד המיגרציה, קריאות וביקורת קיימת נשארות זמינות; פעולת הסיום החדשה נדחית
ב־FORM_APPROVAL_MIGRATION_REQUIRED לפני כתיבה. השרת הישן אינו תומך בנתוני
v2. לאחר יצירתם אין להחזיר שרת ישן או למחוק היסטוריה כדרך rollback.

## React ו־Excel

דגלי השרת, גם בשדות מלאים, מודגשים עם סיבה בעברית ובסינון "דורש בדיקה".
נשארים מקור מוגן, עורך ממוקד וטיוטות. נדרשים שמירת התיקונים, שם/הצהרת בודק,
תיבת הצהרה וכפתור "אישור סופי" אחד לטופס הלוגי. כל שורות ההתחייבות שלו
מקבלות אותו מצב מהשרת. אין הצלחה אופטימית. שגיאות משמרות את הנתונים;
בתשובה לא ידועה נשמרת אותה בקשה לשליחה חוזרת עם אותו מזהה וחותמת זמן.
גרסה ישנה של השרת אינה מקבלת מימוש סיום מדומה בדפדפן.

Excel שומר את ההרחבה הקיימת לשורה לכל מספר התחייבות נבדל. נוספו בסוף
Forms העמודות form_finalized ו־form_approval_basis, וב־ReviewAudit נשמר
אירוע טופס אחד. ערכי המקור והתיקונים לא עוברים המרה למספרים או צימוד לדגימות.
ייצוא מצב חלקי נותר אפשרי ומסומן בהתאם; הוא אינו אישור סופי.

## אימות מקומי ללא ספקים או מסד

מתיקיית הפרויקט המקורית, PowerShell:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_form_approval.py tests/test_business_storage.py tests/test_business_records.py
npm --prefix .\storage\premium-ui-worktree\frontend test
npm --prefix .\storage\premium-ui-worktree\frontend run typecheck
npm --prefix .\storage\premium-ui-worktree\frontend run build
node --experimental-strip-types .\storage\premium-ui-worktree\frontend\tests\form-approval-e2e.ts
node --experimental-strip-types .\storage\premium-ui-worktree\frontend\tests\browser-smoke.ts
```

בדיקת e2e מרנדרת את רכיב React האמיתי ומתקשרת בתהליך בדיקה ל־FastAPI
TestClient אמיתי. נבדקים שני טפסים, חוסר תעודת זהות ותאריך מלא אך לא ודאי,
תיקון ללא נימוק חופשי, שני אישורי טופס, פתיחה חוזרת וארבע שורות XLSX.
הספק מדומה; אין Gemini, מסד או רשת חיצונית. זו אינה בדיקת דפדפן או restart
של PostgreSQL. בדיקת browser-smoke נפרדת משתמשת בשרת תצוגה סינתטי בלבד.

בדיקות PostgreSQL החדשות ב־tests/integration/test_form_approval.py דורשות
DSN מאושר למסד בדיקות מקומי חד־פעמי. הן יוצרות schema ייחודי ובודקות
restart/reopen, זהות XLSX, תחרות, תיקון והיעדר מיגרציה. לא הופעלו במשימה
זו; אסור לתאר את מסלול הסיום כמאומת על PostgreSQL עד להרצתן בהרשאה נפרדת.

עדכון במשימת אימות נפרדת: כל **68 בדיקות PostgreSQL עברו**, ללא כשלונות
או דילוגים, ב־`review_test_viral_lab` בלבד. נוספו גם בדיקות שימור נתונים
קיימים ו־rollback של DDL. המיגרציה לא הופעלה על `viral_lab`.
ראו [דוח האימות](form_approval_migration_validation.md) לפרטי הבידוד,
התאימות והצעד הבא. סעיף הבדיקות המקורי להלן מתעד את מצב משימת המימוש.

תוצאות שבוצעו במשימה: חבילת backend מלאה — 883 עברו, 66 בדיקות PostgreSQL
דולגו בכוונה, ללא טעינת DSN. לאחר הוספת שלוש בדיקות שער מיגרציה נוספות,
הרצה ממוקדת של מדיניות/אחסון/רשומות — 54 עברו. React — 38 עברו; typecheck
ובניית production עברו; בדיקת React→FastAPI→XLSX עברה. בדיקת Chrome מקומית
לרגרסיית הממשק הקיים עברה, כולל RTL, מובייל, חיפוש, דפדוף, ביקורת ושגיאות.
היא משתמשת בשרת התצוגה הסינתטי ואינה מוכיחה לבדה סיום טופס מול PostgreSQL.
אין lint מוגדר בפרויקט. לא הורצה הערכת דיוק קלינית או ניסוי AI; אין טענה
לשיפור דיוק על בסיס בדיקות התוכנה האלה.

להצגת הממשק עם השרת העדכני, רק לאחר אימות המיגרציה בסביבת בדיקה ואישור
פריסה נפרד: מריצים את factory הקיים בענף המקורי ואת Vite בענף העיצוב.
הפקודות אינן טוענות או משנות .env:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn backend.tools.durable_demo:create_durable_demo_app --factory --host 127.0.0.1 --port 8000
npm --prefix .\storage\premium-ui-worktree\frontend run dev
```

פתחו http://127.0.0.1:5173, פתחו רשומה מורשית מהמאגר ללא חילוץ חדש,
פתרו ושמרו את החריגים, בדקו את כלל הערכים מול המקור, מלאו הצהרה וסמנו
אותה, לחצו "אישור סופי", פתחו שוב וייצאו. יצירת חילוץ חי אינה חלק מהאימות.

### הבהרת חסמים ושמירת תיקון — 20.10.2026

שוחזר בבדיקה סינתטית מצב שבו תאריך `18-08-2026` נשמר כ־`approved_corrected`,
אך נשאר חסם `INVALID_DATE`. שמירת החלטת שדה אינה מבטיחה זכאות לאישור טופס:
המדיניות הקיימת מקבלת תאריך מלא ותקין ב־`DD.MM.YYYY`, ב־`DD/MM/YYYY` או
ב־`YYYY-MM-DD`; היא אינה מקבלת יום־חודש־שנה עם מקפים. לא הורחבה המדיניות
ולא מבוצעת המרה אוטומטית. לאחר תיקון מפורש ל־`18.08.2026` ושמירה, חסם
התאריך נעלם. הערות ואי־ודאות מקוריות נשמרות ב־snapshot ובהיסטוריה; הן אינן
נשארות חסם כשמדובר בתיקון אנושי תקין.

בממשק הפרימיום, אזור אישור בולט ודביק נמצא בראש הביקורת. הוא מציג חסמים
לפי `requires_review` ו־`review_reasons` שהשרת מחזיר, עם שמות שדות וקישורים
לפתיחתם. טיוטות מוצגות בנפרד. חסם זכאות אינו מוסק מצבע תא. בהיעדר מצב
מלא לכל שבעת השדות, אין אפשרות לאשר. טיוטה בטופס אחר אינה חוסמת אישור
לטופס הנבחר; השינויים המשותפים לכל התחייבויות אותו טופס עדיין מחייבים שמירה.

"רענון מצב מהשרת" מבצע GET בלבד, ללא חילוץ. טיוטות שלא נשמרו נשארות במסך;
רענון אינו מסמן הצהרה אנושית. אחרי דחיית אישור בשל גרסה או אימות, נדרש
רענון מוצלח והצהרה חדשה. כשל רשת שתוצאתו אינה ידועה ממשיך להשתמש באותה
בקשה ובאותם מזהים לצורך idempotency. לא נוספו API, מיגרציה או אירועי אישור
שדה סמויים, ולא שונה קוד השרת או חוזה הביקורת.

בדיקות הנסיגה כוללות תאריך שנשמר בפורמט הלא נתמך ואז תוקן, שדה ריק,
טיוטות, כשל רענון, גרסה ישנה, ריבוי התחייבויות, אישור יחיד ופתיחה חוזרת.
בדיקת React→FastAPI→XLSX משתמשת בשרת הבדיקות האמיתי עם ספק מדומה; בדיקת
Chrome משתמשת בשרת fixture סינתטי ומסומן. אף אחת אינה ניגשת ל־PostgreSQL.
אין להריץ מיגרציות או לשנות רשומות עסקיות במסגרת בדיקות אלו.

תוצאות ההרצה במשימת תיקון זו: **173 בדיקות backend עברו**, ללא כשלונות או
דילוגים; **43 בדיקות frontend עברו**; typecheck ובניית production עברו;
React→FastAPI→XLSX ו־Chrome עברו. בדיקת Chrome אימתה גם כפתור גלוי ודביק
ב־1440 וב־390 פיקסלים, את החסם לאחר שמירת התאריך ואת השלמת האישור לאחר
תיקונו. הופיעו אזהרות deprecation קיימות בספריות Python. אין lint מוגדר.
לא בוצעו חיבור למסד, מיגרציה, קריאת Gemini או בדיקת restart של PostgreSQL.

לאימות ידני, עצרו את השרת הקיים ב־Ctrl+C והפעילו מהתיקייה המקורית בשני
חלונות PowerShell. הפקודה הראשונה קוראת את `.env` הקיים ואינה משנה אותו:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn backend.tools.durable_demo:create_durable_demo_app --factory --host 127.0.0.1 --port 8000 --env-file .env --no-access-log --no-proxy-headers
npm --prefix .\storage\premium-ui-worktree\frontend run dev
```

ב־http://127.0.0.1:5173 פתחו רשומה שמורה; אין להעלות מסמך או להפעיל חילוץ.
לחצו על חסם תאריך, בחרו "תיקון ואישור", הזינו תאריך מאומת בפורמט נתמך
ושמרו. לחצו "רענון מצב מהשרת", בדקו את כל החסמים ששמותיהם מוצגים,
מלאו פרטי בודק וסמנו הצהרה מפורשת. אם אין חסמים או טיוטות בטופס הנבחר,
"אישור סופי" יהיה פעיל. לאחר אישור פתחו שוב וייצאו; זו פעולה אנושית
המשנה את הרשומה הנבחרת, ואינה חלק מהבדיקות האוטומטיות שבוצעו במשימה.

# Infinite Academy — ابدأ من هنا

**المسار المقترح:** Render Free للموقع + Supabase Free لقاعدة PostgreSQL + GitHub Actions لبناء APK.

1. ارفع محتويات مجلد المشروع إلى مستودع GitHub خاصاً، مع عدم رفع `.env` أو أي ملف يحتوي سراً.
2. أنشئ Supabase واستخدم Connect → Shared Pooler → Session mode لنسخ رابط PostgreSQL.
3. أنشئ Blueprint في Render من المستودع؛ سيقرأ `render.yaml`. أدخل `DATABASE_URL_MAIN`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `VDOCIPHER_API_SECRET` عند الحاجة، و`SUPPORT_WHATSAPP_NUMBER`. Render يولّد `SECRET_KEY` تلقائياً من إعدادات الملف.
4. انتظر نجاح النشر وافتح `/health`. يجب أن يعود `200` و`status: ok`.
5. GitHub → Actions → Build Infinite Academy APK → Run workflow، وأدخل رابط الموقع `https://...onrender.com` في `academy_base_url`.

اقرأ [`RENDER_SUPABASE_GITHUB_ACTIONS_SETUP_AR.md`](RENDER_SUPABASE_GITHUB_ACTIONS_SETUP_AR.md) للتفاصيل والحدود المجانية.

**مهم:** Render Free قد يوقف الخدمة بعد 15 دقيقة من الخمول، وقد يتأخر أول طلب حتى تستيقظ. Supabase Free قد يوقف مشروعاً منخفض النشاط بعد 7 أيام تقريباً. هذه الخطط مناسبة للتجربة، وليست ضمان استمرارية إنتاجية.

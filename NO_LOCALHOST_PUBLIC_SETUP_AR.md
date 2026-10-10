# التشغيل العام بدون localhost

`localhost` مخصص للاختبار على الهاتف أو الكمبيوتر نفسه. الموقع العام يجب أن يعمل على خادم مستضاف بعنوان HTTPS.

## المسار المختار للتجربة المجانية

- **Render:** تشغيل Flask/Gunicorn والحصول على HTTPS تلقائياً.
- **Supabase:** PostgreSQL دائمة لبيانات الطلاب والأجهزة والدورات.
- **GitHub Actions:** بناء APK التجريبي بعد نشر رابط HTTPS.

اتبع الخطوات الكاملة في [RENDER_SUPABASE_GITHUB_ACTIONS_SETUP_AR.md](RENDER_SUPABASE_GITHUB_ACTIONS_SETUP_AR.md).

لا تستخدم SQLite المحلية في خدمة Render المجانية؛ نظام ملفاتها مؤقت وتضيع التغييرات عند إعادة التشغيل أو السكون. استخدم `DATABASE_URL_MAIN` من Supabase.

## رابط APK

بعد النشر، أدخل `https://YOUR-SERVICE.onrender.com` في حقل `academy_base_url` ضمن GitHub Actions → Build Infinite Academy APK → Run workflow. لا تدخل `127.0.0.1` أو `localhost`.

## ما لم يتم فعله تلقائياً

لم ينشئ هذا الملف حسابات GitHub أو Render أو Supabase نيابةً عنك، ولم ينشر الموقع بالفعل؛ يجب تسجيل الدخول بحساباتك وإضافة أسرار التشغيل داخل لوحة Render. لا تشارك `DATABASE_URL_MAIN` أو `SECRET_KEY` أو مفتاح VdoCipher في المحادثة.

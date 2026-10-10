# Infinite Academy — ابدأ من هنا لبناء APK عبر الإنترنت

## الاختيار الأنسب لهذا المشروع

**Render (الخطة المجانية للتجربة) لاستضافة Flask + Supabase PostgreSQL + GitHub Actions لبناء APK من مشروع Android الأصلي.**

لم أختر محوّل URL عامّاً مثل AppsGeyser لهذا المشروع، لأن Infinite Academy لا يحتاج مجرد WebView؛ بل يحتوي على جسر Android أصلي ومفتاح توقيع داخل Android Keystore لإثبات هوية تثبيت التطبيق. محوّل يعتمد على رابط الموقع فقط لن يضمن تضمين هذا الجسر أو تنفيذ بروتوكول إثبات المفتاح.

## لماذا لا أستطيع إعطاءك APK يعمل من هذا الملف وحده؟

الخادم Flask يحتاج إلى عنوان عام عبر HTTPS وقاعدة بيانات دائمة. الـAPK سيُبنى وفي داخله عنوان الموقع الذي سيُفتحه. إذا استُخدم `127.0.0.1` أو عنوان تجريبي، فلن يعمل للطلاب عبر الإنترنت. لا تضع كلمات مرور Supabase أو مفتاح VdoCipher داخل APK.

## التنفيذ بالترتيب

### A. ارفع الكود إلى GitHub من Termux

1. أنشئ مستودعاً فارغاً وPrivate في GitHub.
2. استخرج ZIP وضع محتويات مجلد `infinite-academy-supabase` في مجلد عملك. يجب أن يكون `app.py` و`android-app/` و`.github/` في جذر المستودع.
3. شغّل:

```bash
cd ~/infinite-academy/infinite-academy-supabase
git init
git add .
git status --short
```

راجع القائمة وتأكد أن `.env` وقواعد البيانات المحلية غير موجودة. ثم:

```bash
git commit -m "Prepare Infinite Academy for hosted web and APK"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

استبدل معلومات المستودع بقيمك. لا تشارك رمز الوصول أو كلمة المرور.

### B. انشر موقع Flask على Render

1. أنشئ مشروع Supabase PostgreSQL وانسخ connection string من لوحة Connect.
2. في Render استخدم New → Blueprint واختر المستودع؛ سيقرأ إعدادات `render.yaml`. أو أنشئ Web Service، ثم اضبط Build=`pip install -r requirements.txt` وStart=`gunicorn app:app --workers 1 --threads 4 --timeout 120`.
3. أضف متغيرات الإنتاج في Render → Environment، على الأقل:
   - `DATABASE_URL_MAIN`: رابط Supabase الحقيقي.
   - `SECRET_KEY`: قيمة عشوائية طويلة وفريدة.
   - `ADMIN_EMAIL`: بريد الأدمن الذي تريد إنشاؤه أول مرة.
   - `ADMIN_PASSWORD`: كلمة مرور قوية من 12 إلى 128 محرفاً.
   - `COOKIE_SECURE=true`.
   - `VDOCIPHER_API_SECRET`: المفتاح السري من حساب VdoCipher عندما تريد تشغيل الفيديو فعلياً.
   - `SUPPORT_WHATSAPP_NUMBER`: رقم الدعم الدولي بالأرقام فقط، إذا أردت رابط دعم.
4. استخدم نطاق `onrender.com` الذي تعرضه Render؛ HTTPS يُدار تلقائياً. افحص `/health` قبل بناء APK.
5. اختبر `https://YOUR-DOMAIN/health`. يجب أن يرجع 200 وحالة سليمة. بعد ذلك اختبر التسجيل وتفعيل الأدمن والأجهزة. الفيديو يحتاج إعداد VdoCipher صحيحاً.

### C. ابنِ APK من GitHub Actions

1. في GitHub افتح المستودع ثم Actions.
2. اختر **Build Infinite Academy APK** ثم **Run workflow**.
3. سيظهر حقل `academy_base_url`. أدخل أصل الموقع الحقيقي فقط، مثلاً `https://infinite-academy.onrender.com`، من دون `/health` أو أي مسار.
4. اضغط **Run workflow** وانتظر نجاح البناء.
5. افتح العملية الناجحة وانزل إلى **Artifacts**، ثم نزّل `infinite-academy-debug-apk`.
6. فك ضغط الـArtifact وثبّت `app-debug.apk` على هاتف Android للتجربة.

## ما الذي تحصل عليه؟

- APK تجريبي يفتح موقعك المنشور عبر HTTPS.
- مصدر Android يوفّر UUID للتثبيت ومفتاح ECDSA خاصاً في Android Keystore.
- مصدر Flask للتحقق من إثبات المفتاح من جهة الخادم.

**هذا ليس إصداراً نهائياً لـGoogle Play.** يجب اختبار APK على هاتف حقيقي، وتجربة تسجيل الدخول والخروج وفقدان Cookies واعتماد الجهاز وتبديله وتشغيل VdoCipher. لم تُنشأ شهادة توقيع إصدار Release ولم يُبنَ APK داخل هذه الحزمة.

## روابط الخدمات

- Render: https://render.com/
- GitHub: https://github.com/new
- Supabase: https://supabase.com/dashboard
- توثيق نشر Flask في Render: https://render.com/docs/deploy-flask
- دليل Render + Supabase + GitHub Actions: `RENDER_SUPABASE_GITHUB_ACTIONS_SETUP_AR.md`

## لا ترفع هذه الملفات

لا ترفع `.env` أو قواعد بيانات `.db` المحلية أو مفاتيح VdoCipher أو ملفات `local.properties` التي قد تحتوي إعدادات جهازك. لا تضع أسرار الخادم في متغيرات Android أو في التطبيق.

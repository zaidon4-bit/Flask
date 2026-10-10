# نشر Infinite Academy مجاناً: Render + Supabase + GitHub Actions

هذه الحزمة مجهزة ليعمل Flask على Render، وتُخزّن البيانات في Supabase PostgreSQL، ويُبنى APK عبر GitHub Actions. لا يلزم PythonAnywhere لهذا المسار.

## ما الذي ستحصل عليه؟

- رابط HTTPS مجاني من نوع `https://اسم-الخدمة.onrender.com` مع شهادة TLS مُدارة تلقائياً.
- Supabase PostgreSQL لتخزين الحسابات والأجهزة والدورات والسجلات.
- GitHub Actions لبناء APK تجريبي مرتبط برابط الموقع العام.

## 1) أنشئ Supabase PostgreSQL

1. افتح https://supabase.com/dashboard وأنشئ مشروعاً جديداً.
2. احتفظ بكلمة مرور قاعدة البيانات بأمان.
3. في صفحة Connect اختر Shared Pooler ثم Session mode، وانسخ رابط PostgreSQL كاملاً. هذا خيار مناسب لاتصال IPv4 بالخطة المجانية؛ استخدم الرابط الذي تعرضه لوحة Supabase حرفياً، ولا تخمّن اسم المضيف أو اسم المستخدم.
4. تأكد أن الرابط يستخدم `postgresql://` أو `postgres://`، وأضف `?sslmode=require` إذا لم يتضمن فرض TLS بالفعل. لا تضع الرابط في الكود أو في GitHub.

## 2) ارفع المشروع إلى GitHub

1. أنشئ مستودعاً خاصاً (Private) في https://github.com/new.
2. فك ضغط ZIP وارفع **محتويات** مجلد `infinite-academy-supabase` إلى جذر المستودع. يجب أن يظهر `app.py` و`render.yaml` و`.github/` في جذر المستودع.
3. لا ترفع `.env` أو قواعد `.db` المحلية أو مفاتيح الخدمات. يحتوي `.gitignore` على قواعد استبعادها، لكن راجع `git status` قبل الـpush.

## 3) انشر على Render

1. افتح https://dashboard.render.com/ واربط حساب GitHub.
2. استخدم **New → Blueprint**، واختر المستودع الذي رفعته. سيقرأ Render ملف `render.yaml`. إذا لم يظهر Blueprint، استخدم **New → Web Service** واختر المستودع، Build command=`pip install -r requirements.txt` وStart command=`gunicorn app:app --workers 1 --threads 4 --timeout 120`، واختر الخطة Free.
3. أكمل إنشاء الخدمة، ثم افتح Environment/Variables. أضف القيم التالية (الموجودة في `render.yaml` سيطلب Render القيم السرية غير المتزامنة منها أثناء الإعداد أو من صفحة Environment):

   - `DATABASE_URL_MAIN`: رابط Session Pooler من Supabase.
   - `SECRET_KEY`: يولّده Render تلقائياً عبر `generateValue: true`؛ لا تستبدله بقيمة عامة.
   - `ADMIN_EMAIL`: البريد الذي ستستخدمه لحساب الأدمن الأول.
   - `ADMIN_PASSWORD`: كلمة مرور فريدة بين 12 و128 محرفاً.
   - `VDOCIPHER_API_SECRET`: المفتاح السري من لوحة VdoCipher. اتركه فارغاً فقط إذا لم تختبر الفيديو بعد، وتذكّر أن تشغيل الفيديو المحمي لن يعمل بدونه.
   - `SUPPORT_WHATSAPP_NUMBER`: رقم الدعم الدولي أرقاماً فقط، دون `+` أو مسافات.
   - تأكد أن `COOKIE_SECURE=true`.

4. بعد اكتمال النشر، افتح عنوان `onrender.com` الذي يظهر في لوحة الخدمة. Render يوفر TLS/HTTPS تلقائياً.
5. افتح `https://YOUR-SERVICE.onrender.com/health`. يجب أن يرجع HTTP 200 و`status: ok` وكل قواعد البيانات `ok`. إذا رجع `degraded` أو 503، راجع سجل النشر واتصال Supabase، ولا تبدأ بناء APK بعد.

## 4) ابنِ APK عبر GitHub Actions

1. افتح المستودع على GitHub → **Actions**.
2. اختر **Build Infinite Academy APK** ثم **Run workflow**.
3. في `academy_base_url` ضع أصل الموقع HTTPS فقط، مثال: `https://infinite-academy.onrender.com`، بلا مسار أو شرطة أخيرة.
4. بعد نجاح المهمة، افتحها ثم نزّل Artifact باسم `infinite-academy-debug-apk` وفك ضغطه إلى `app-debug.apk`.
5. ثبّت APK واختبر تسجيل الدخول، طلب موافقة جهاز ثانٍ، استمرارية معرّف التثبيت بعد إغلاق التطبيق، واستعادة الدخول بعد حذف الكوكيز. لا توزعه للطلاب قبل نجاح الاختبارات على جهاز حقيقي.

## حدود الخطة المجانية (مهمة)

- خدمة Render المجانية تتوقف بعد نحو 15 دقيقة من عدم استقبال طلبات، وأول طلب بعدها قد يحتاج قرابة دقيقة حتى تستيقظ. ملفات النظام المحلية مؤقتة وتُفقد عند إعادة التشغيل؛ لذلك لا تستخدم SQLite للإنتاج هنا. يجب أن يكون `DATABASE_URL_MAIN` رابط Supabase فعلياً.
- Supabase Free قد يوقف المشروع إذا كان النشاط منخفضاً مدة تقارب 7 أيام، كما أن موارد الخطة محدودة؛ احتفظ بنسخ احتياطية مناسبة واختبر استعادة البيانات.
- Render يمنح HTTPS على نطاق `onrender.com` مجاناً. لا تحتاج شراء نطاق مخصص للتجربة.
- `VDOCIPHER_API_SECRET` يبقى على الخادم فقط، ولا يوضع داخل APK أو GitHub.
- هذا إعداد للتجربة/المشروع الشخصي وليس ضماناً لاستضافة إنتاجية متاحة دائماً. تحقق من الأسعار والحدود الحالية لدى Render وSupabase قبل استعماله مع طلاب يدفعون مالاً.

## مصادر رسمية

- Render Flask: https://render.com/docs/deploy-flask
- Render Free: https://render.com/docs/free
- Render TLS: https://render.com/docs/tls
- Supabase Postgres connections: https://supabase.com/docs/guides/database/connecting-to-postgres
- Supabase project pausing: https://supabase.com/docs/guides/platform/free-project-pausing

# بناء APK من الهاتف باستخدام GitHub Actions

هذه الطريقة تبني APK تجريبياً من الهاتف عبر GitHub Actions؛ لا تحتاج Android Studio على الهاتف. يجب نشر موقع Flask أولاً على عنوان HTTPS حقيقي. تشغيل الموقع على `127.0.0.1:5000` وحده لا يكفي لتطبيق موزّع. راجع أيضاً [دليل البداية السريع](BUILD_APK_ONLINE_START_HERE_AR.md).

## 1. جهّز رابط الموقع

انشر Flask على Render (الخطة المجانية للتجربة) أو استضافة HTTPS أخرى، ثم اختبر `https://YOUR-DOMAIN/health` وتأكد أن الصفحة تعمل. لا تضع أسرار Supabase أو VdoCipher في إعدادات تطبيق Android.

## 2. ارفع ملفات المشروع إلى GitHub من Termux

بعد إنشاء مستودع فارغ في GitHub، انتقل إلى مجلد `infinite-academy-supabase` المستخرج. لا ترفع ملف `.env` أو قاعدة بيانات محلية.

```bash
pkg install git -y
cd ~/infinite-academy/infinite-academy-supabase
git init
git add .
git status --short
```

تأكد أن `.env` وملفات `.db` غير ظاهرة ضمن الملفات التي ستُرفع. ثم:

```bash
git commit -m "Prepare Infinite Academy and Android app"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

استبدل الرابط برابط مستودعك. قد يطلب GitHub وسيلة مصادقة؛ لا ترسل أي رمز وصول أو كلمة مرور إلى أي شخص.

## 3. أضف متغير رابط الموقع

الأسهل: من تبويب **Actions** اختر **Build Infinite Academy APK → Run workflow**، ثم أدخل رابط HTTPS الحقيقي في حقل `academy_base_url` (الأصل فقط، دون مسار).

ويمكنك بدلاً من ذلك إضافة Repository Variable باسم `ACADEMY_BASE_URL` من **Settings → Secrets and variables → Actions → Variables** إذا كنت تريد البناء التلقائي لاحقاً. لا تضع `.env` أو أسرار قاعدة البيانات في هذا المتغير.

## 4. شغّل بناء APK

افتح تبويب **Actions** في مستودع GitHub، واختر **Build Infinite Academy APK**، ثم **Run workflow**. انتظر حتى ينتهي البناء بنجاح.

افتح تشغيل الـworkflow الناجح، وانزل إلى **Artifacts**، ثم نزّل `infinite-academy-debug-apk`. فك ضغط الملف على الهاتف وثبّت `app-debug.apk`.

## 5. تنبيهات

- هذا APK تجريبي موقّع بمفتاح debug، وليس إصداراً نهائياً للنشر على Google Play.
- يجب أن يكون عنوان الموقع متاحاً من الإنترنت عبر HTTPS؛ العنوان `127.0.0.1` لن يشير إلى خادم Termux من جهاز آخر.
- اختبر على هاتف فعلي تسجيل الدخول، الموافقة على الجهاز، فقدان كوكيز WebView، تسجيل الخروج، واستبدال الجهاز.
- عند نشر الموقع لأول مرة على قاعدة بيانات دائمة، خذ نسخة احتياطية قبل تحديث المخطط.

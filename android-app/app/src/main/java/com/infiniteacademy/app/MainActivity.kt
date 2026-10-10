package com.infiniteacademy.app

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Build
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import android.graphics.Color
import android.util.Base64
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.UUID

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private val baseUri: Uri by lazy { Uri.parse(BuildConfig.ACADEMY_BASE_URL) }
    private val keyAlias = "infinite_academy_device_signing_v1"
    private val prefs by lazy { getSharedPreferences("infinite_academy_install", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Block screenshots, screen recording and the recent-apps preview for the whole app.
        window.setFlags(
            android.view.WindowManager.LayoutParams.FLAG_SECURE,
            android.view.WindowManager.LayoutParams.FLAG_SECURE
        )
        if (BuildConfig.ACADEMY_BASE_URL.contains(".invalid")) {
            showMessage(
                "قبل بناء التطبيق، حدّد رابط موقع Infinite Academy المنشور.\n\n" +
                    "أضف academyBaseUrl=https://your-real-domain إلى ملف local.properties"
            )
            return
        }
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            showMessage("مكوّن Android System WebView قديم. حدّث Android System WebView ثم افتح التطبيق مجدداً.")
            return
        }
        setupWebView()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView = WebView(this)
        webView.setBackgroundColor(Color.rgb(8, 11, 18))
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                safeBrowsingEnabled = true
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false)

        WebViewCompat.addWebMessageListener(
            webView,
            "InfiniteAcademyNative",
            setOf(trustedOrigin()),
            object : WebViewCompat.WebMessageListener {
                override fun onPostMessage(
                    view: WebView,
                    message: WebMessageCompat,
                    sourceOrigin: Uri,
                    isMainFrame: Boolean,
                    replyProxy: JavaScriptReplyProxy,
                ) {
                    if (!isMainFrame || !isTrustedOrigin(sourceOrigin)) return
                    handleBridgeMessage(message.data, replyProxy)
                }
            },
        )

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Let subframe navigations (for example the VdoCipher player iframe) remain in WebView.
                if (!request.isForMainFrame) return false
                val uri = request.url
                if (isTrustedOrigin(uri)) return false
                // The Android bridge is not exposed to an external origin. Open external links outside WebView.
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, uri))
                    true
                } catch (e: Exception) {
                    true
                }
            }
        }
        setContentView(webView, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        webView.loadUrl(BuildConfig.ACADEMY_BASE_URL)
    }

    private fun trustedOrigin(): String = "${baseUri.scheme}://${baseUri.host}${if (baseUri.port > 0) ":${baseUri.port}" else ""}"

    private fun isTrustedOrigin(uri: Uri): Boolean {
        if (uri.scheme != "https" || uri.host != baseUri.host) return false
        val actualPort = if (uri.port == -1) 443 else uri.port
        val expectedPort = if (baseUri.port == -1) 443 else baseUri.port
        return actualPort == expectedPort && uri.userInfo == null
    }

    private fun handleBridgeMessage(rawMessage: String?, replyProxy: JavaScriptReplyProxy) {
        var requestId = ""
        try {
            val request = JSONObject(rawMessage ?: "{}")
            requestId = request.optString("requestId", "")
            val type = request.optString("type", "")
            val response = when (type) {
                "get-device-identity" -> {
                    val pair = loadOrCreateKeyPair()
                    val installId = loadOrCreateInstallationId(pair.public.encoded)
                    JSONObject()
                        .put("installationId", installId)
                        .put("publicKey", base64Url(pair.public.encoded))
                }
                "sign-device-challenge" -> {
                    val challenge = decodeBase64Url(request.optString("challenge", ""))
                    require(challenge.size == 32) { "Invalid challenge" }
                    JSONObject().put("signature", signWithKeystore(challenge))
                }
                else -> throw IllegalArgumentException("Unsupported request")
            }
            response.put("requestId", requestId).put("ok", true)
            replyProxy.postMessage(response.toString())
        } catch (e: Exception) {
            val response = JSONObject()
                .put("requestId", requestId)
                .put("ok", false)
                .put("error", "native_operation_failed")
            replyProxy.postMessage(response.toString())
        }
    }

    private fun loadOrCreateInstallationId(publicKeyDer: ByteArray): String {
        val publicKeyHash = java.security.MessageDigest.getInstance("SHA-256")
            .digest(publicKeyDer).joinToString("") { byte -> "%02x".format(byte) }
        val previousKeyHash = prefs.getString("public_key_hash", null)
        val savedInstallId = prefs.getString("installation_id", null)
        // If the Keystore key disappeared or was regenerated while preferences survived,
        // rotate the install ID. The server then asks the admin to review it as a new device.
        val current: String = if (savedInstallId == null ||
            (previousKeyHash != null && previousKeyHash != publicKeyHash)) {
            UUID.randomUUID().toString()
        } else {
            savedInstallId
        }
        prefs.edit()
            .putString("installation_id", current)
            .putString("public_key_hash", publicKeyHash)
            .apply()
        return current
    }

    private fun loadOrCreateKeyPair(): KeyPair {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existingPrivate = keyStore.getKey(keyAlias, null) as? PrivateKey
        val existingCertificate = keyStore.getCertificate(keyAlias)
        if (existingPrivate != null && existingCertificate != null) {
            return KeyPair(existingCertificate.publicKey, existingPrivate)
        }
        // Private key material is generated and retained by Android Keystore; it is never exported to JS.
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        generator.initialize(
            KeyGenParameterSpec.Builder(
                keyAlias,
                KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
            )
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .build()
        )
        return generator.generateKeyPair()
    }

    private fun signWithKeystore(message: ByteArray): String {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val privateKey = keyStore.getKey(keyAlias, null) as? PrivateKey
            ?: throw IllegalStateException("Keystore key is unavailable")
        val signer = Signature.getInstance("SHA256withECDSA")
        signer.initSign(privateKey)
        signer.update(message)
        return base64Url(signer.sign())
    }

    private fun base64Url(value: ByteArray): String =
        Base64.encodeToString(value, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)

    private fun decodeBase64Url(value: String): ByteArray =
        Base64.decode(value, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)

    private fun showMessage(message: String) {
        val text = TextView(this).apply {
            text = message
            setTextColor(Color.WHITE)
            textSize = 16f
            gravity = Gravity.CENTER
            setPadding(32, 32, 32, 32)
            setBackgroundColor(Color.rgb(8, 11, 18))
        }
        setContentView(text, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }

    @Deprecated("Deprecated in Android 13; retained for broad platform compatibility")
    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }
}

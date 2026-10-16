package com.auri.chat

import android.graphics.Color as AndroidColor
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import java.io.ByteArrayInputStream

@Composable
internal fun ReportWebView(html: String, modifier: Modifier, onContentHeight: ((Float) -> Unit)? = null) {
    val view = remember { mutableStateOf<WebView?>(null) }
    val heightCallback by rememberUpdatedState(onContentHeight)
    DisposableEffect(Unit) { onDispose { view.value?.apply { stopLoading(); destroy() }; view.value = null } }
    AndroidView(modifier = modifier, factory = { context ->
        WebView(context).apply {
            view.value = this
            setBackgroundColor(AndroidColor.rgb(16, 16, 28))
            settings.javaScriptEnabled = false
            settings.domStorageEnabled = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.blockNetworkLoads = true
            settings.setSupportZoom(true)
            settings.builtInZoomControls = true
            settings.displayZoomControls = false
            if (onContentHeight != null) {
                settings.setSupportZoom(false)
                isVerticalScrollBarEnabled = false
                overScrollMode = WebView.OVER_SCROLL_NEVER
                setOnTouchListener { touched, _ ->
                    touched.parent?.requestDisallowInterceptTouchEvent(false)
                    false
                }
            }
            fun measureReport() {
                if (view.value !== this || contentHeight <= 0) return
                @Suppress("DEPRECATION")
                val heightDp = contentHeight * scale / resources.displayMetrics.density
                heightCallback?.invoke(heightDp)
            }
            addOnLayoutChangeListener { _, left, _, right, _, oldLeft, _, oldRight, _ ->
                if (right - left != oldRight - oldLeft) post { measureReport() }
            }
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(web: WebView?, url: String?) {
                    if (heightCallback != null) {
                        // Layout can settle after onPageFinished; bounded native measurements
                        // let the entire document follow chat scrolling without JavaScript.
                        for (delayMs in listOf(0L, 100L, 300L, 700L)) {
                            postDelayed({ measureReport() }, delayMs)
                        }
                    }
                }
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?) = true
                override fun shouldInterceptRequest(view: WebView?, request: WebResourceRequest?) = WebResourceResponse("text/plain", "UTF-8", ByteArrayInputStream(ByteArray(0)))
            }
        }
    }, update = { web ->
        if (web.tag != html) {
            web.tag = html
            web.loadDataWithBaseURL("https://reports.auri.invalid/", html, "text/html", "UTF-8", null)
        }
    })
}

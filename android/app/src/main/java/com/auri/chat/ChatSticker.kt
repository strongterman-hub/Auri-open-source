package com.auri.chat

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.core.graphics.drawable.toBitmap
import coil.compose.AsyncImagePainter
import coil.compose.AsyncImage
import coil.compose.rememberAsyncImagePainter
import coil.request.ImageRequest
import coil.ImageLoader
import coil.decode.GifDecoder
import coil.decode.ImageDecoderDecoder
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.roundToInt

private object StickerImages {
    @Volatile private var instance: ImageLoader? = null
    fun loader(context: android.content.Context): ImageLoader = instance ?: synchronized(this) {
        instance ?: ImageLoader.Builder(context.applicationContext).components {
            if (android.os.Build.VERSION.SDK_INT >= 28) add(ImageDecoderDecoder.Factory())
            else add(GifDecoder.Factory())
        }.build().also { instance = it }
    }
}

data class ChatSticker(
    val id: String,
    val url: String,
    val description: String,
    val crop: List<Float> = listOf(0f, 0f, 1f, 1f),
    val animated: Boolean = false,
) {
    fun encode(): String = JSONObject().put("id", id).put("url", url)
        .put("description", description).put("crop", JSONArray(crop)).put("animated", animated).toString()
}

internal fun parseChatSticker(value: String): ChatSticker? = runCatching {
    if (value.isBlank()) return null
    val obj = JSONObject(value)
    val rawUrl = obj.optString("url")
    val origin = resolveImageUrl("/static/expression/")
    val url = resolveImageUrl(rawUrl)
    if (!url.startsWith(origin) || obj.optString("id").isBlank()) return null
    val array = obj.optJSONArray("crop")
    val crop = if (array != null && array.length() == 4) {
        List(4) { array.optDouble(it, Double.NaN).toFloat() }
    } else listOf(0f, 0f, 1f, 1f)
    if (crop.any { !it.isFinite() || it < 0f || it > 1f } ||
        crop[2] <= 0f || crop[3] <= 0f || crop[0] + crop[2] > 1.001f || crop[1] + crop[3] > 1.001f
    ) return null
    ChatSticker(obj.getString("id"), url, obj.optString("description", "表情"), crop, obj.optBoolean("animated", false))
}.getOrNull()

@Composable
internal fun StickerBubble(sticker: ChatSticker) {
    var expanded by remember(sticker.id) { mutableStateOf(false) }
    Box(Modifier.size(148.dp).clickable { expanded = true }, contentAlignment = Alignment.Center) {
        StickerArtwork(sticker, Modifier.size(144.dp))
    }
    if (expanded) {
        Dialog(onDismissRequest = { expanded = false }) {
            StickerArtwork(sticker, Modifier.size(300.dp).clickable { expanded = false })
        }
    }
}

@Composable
private fun StickerArtwork(sticker: ChatSticker, modifier: Modifier) {
    val context = LocalContext.current
    if (sticker.animated) {
        val animatedLoader = remember(context.applicationContext) { StickerImages.loader(context) }
        var failed by remember(sticker.url) { mutableStateOf(false) }
        Box(modifier, contentAlignment = Alignment.Center) {
            if (failed) Text("[${sticker.description}]", color = MaterialTheme.colorScheme.onSurfaceVariant)
            else AsyncImage(model = sticker.url, imageLoader = animatedLoader,
                contentDescription = sticker.description, contentScale = ContentScale.Fit,
                modifier = Modifier.matchParentSize(), onError = { failed = true })
        }
        return
    }
    val request = remember(sticker.url) {
        // The atlas painter is consumed by Canvas, so it is never drawn as a
        // painter. Resolve size explicitly instead of waiting for painter.onDraw.
        ImageRequest.Builder(context).data(sticker.url).size(coil.size.Size.ORIGINAL)
            .allowHardware(false).build()
    }
    val painter = rememberAsyncImagePainter(request)
    val success = painter.state as? AsyncImagePainter.State.Success
    val bitmap = remember(success) { success?.result?.drawable?.toBitmap()?.asImageBitmap() }
    Box(modifier, contentAlignment = Alignment.Center) {
        if (bitmap == null) {
            Text(if (painter.state is AsyncImagePainter.State.Error) "[${sticker.description}]" else "…",
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Canvas(Modifier.matchParentSize().semantics { contentDescription = sticker.description }) {
                val x = (sticker.crop[0] * bitmap.width).roundToInt().coerceIn(0, bitmap.width - 1)
                val y = (sticker.crop[1] * bitmap.height).roundToInt().coerceIn(0, bitmap.height - 1)
                val width = (sticker.crop[2] * bitmap.width).roundToInt().coerceIn(1, bitmap.width - x)
                val height = (sticker.crop[3] * bitmap.height).roundToInt().coerceIn(1, bitmap.height - y)
                val scale = minOf(size.width / width, size.height / height)
                val dstWidth = (width * scale).roundToInt().coerceAtLeast(1)
                val dstHeight = (height * scale).roundToInt().coerceAtLeast(1)
                drawImage(bitmap, srcOffset = IntOffset(x, y), srcSize = IntSize(width, height),
                    dstOffset = IntOffset(((size.width-dstWidth)/2).roundToInt(), ((size.height-dstHeight)/2).roundToInt()),
                    dstSize = IntSize(dstWidth, dstHeight))
            }
        }
    }
}

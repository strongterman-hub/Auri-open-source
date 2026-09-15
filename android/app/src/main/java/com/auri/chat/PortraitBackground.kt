package com.auri.chat

import android.content.Context
import org.json.JSONObject

/** One resolved smart-background image returned by GET /v1/portrait/current. */
data class PortraitBackground(
    val presentation: String,
    val variant: String,
    val imageUrl: String,
    val imageUrlSmall: String,
    val expiresInSeconds: Int,
)

/** SharedPreferences cache so a cold start renders the last known background. */
class PortraitStore(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("auri_settings", Context.MODE_PRIVATE)

    fun load(): PortraitBackground? {
        val imageUrl = prefs.getString(KEY_IMAGE_URL, null)?.takeIf { it.isNotBlank() }
            ?: return null
        return PortraitBackground(
            presentation = prefs.getString(KEY_PRESENTATION, "female") ?: "female",
            variant = prefs.getString(KEY_VARIANT, "day_gentle") ?: "day_gentle",
            imageUrl = imageUrl,
            imageUrlSmall = prefs.getString(KEY_IMAGE_URL_SMALL, imageUrl) ?: imageUrl,
            expiresInSeconds = prefs.getInt(KEY_EXPIRES_IN, 900),
        )
    }

    fun save(portrait: PortraitBackground) {
        prefs.edit()
            .putString(KEY_PRESENTATION, portrait.presentation)
            .putString(KEY_VARIANT, portrait.variant)
            .putString(KEY_IMAGE_URL, portrait.imageUrl)
            .putString(KEY_IMAGE_URL_SMALL, portrait.imageUrlSmall)
            .putInt(KEY_EXPIRES_IN, portrait.expiresInSeconds)
            .apply()
    }

    fun clear() {
        prefs.edit()
            .remove(KEY_PRESENTATION)
            .remove(KEY_VARIANT)
            .remove(KEY_IMAGE_URL)
            .remove(KEY_IMAGE_URL_SMALL)
            .remove(KEY_EXPIRES_IN)
            .apply()
    }

    fun isSmartBackgroundEnabled(): Boolean =
        prefs.getBoolean(KEY_SMART_BACKGROUND_ENABLED, true)

    fun setSmartBackgroundEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_SMART_BACKGROUND_ENABLED, enabled).apply()
    }

    private companion object {
        const val KEY_PRESENTATION = "portrait_presentation"
        const val KEY_VARIANT = "portrait_variant"
        const val KEY_IMAGE_URL = "portrait_image_url"
        const val KEY_IMAGE_URL_SMALL = "portrait_image_url_small"
        const val KEY_EXPIRES_IN = "portrait_expires_in"
        const val KEY_SMART_BACKGROUND_ENABLED = "smart_background_enabled"
    }
}

/**
 * Parse /v1/portrait/current. Returns null when the server disabled the feature
 * for this account (or the payload is unusable), which means the client must
 * silently fall back to the existing gradient background.
 */
private fun resolveImageUrl(value: String): String {
    if (value.startsWith("http://") || value.startsWith("https://")) return value
    val base = ApiConfig.BASE_URL.trimEnd('/')
    val origin = if (base.endsWith("/v1")) base.dropLast(3) else base
    return origin.trimEnd('/') + if (value.startsWith("/")) value else "/$value"
}

internal fun parsePortrait(json: JSONObject): PortraitBackground? {
    if (!json.optBoolean("enabled", false)) return null
    val rawImageUrl = json.optString("image_url").takeIf { it.isNotBlank() }
        ?: return null
    val imageUrl = resolveImageUrl(rawImageUrl)
    val variant = json.optString("variant", "day_gentle").takeIf { it.isNotBlank() }
        ?: "day_gentle"
    val presentation = json.optString("presentation", "female").takeIf { it.isNotBlank() }
        ?: "female"
    return PortraitBackground(
        presentation = presentation,
        variant = variant,
        imageUrl = imageUrl,
        imageUrlSmall = json.optString("image_url_small", rawImageUrl)
            .takeIf { it.isNotBlank() }
            ?.let(::resolveImageUrl)
            ?: imageUrl,
        expiresInSeconds = json.optInt("expires_in_seconds", 900).coerceAtLeast(60),
    )
}

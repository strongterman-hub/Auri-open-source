package com.auri.chat

internal const val DEVICE_REGISTRATION_REFRESH_MS = 24L * 60L * 60L * 1000L

internal fun shouldRefreshDeviceRegistration(
    registrationId: String,
    lastRegisteredId: String?,
    lastRegisteredAt: Long,
    now: Long,
): Boolean {
    if (registrationId != lastRegisteredId) return true
    if (lastRegisteredAt <= 0L || now < lastRegisteredAt) return true
    return now - lastRegisteredAt >= DEVICE_REGISTRATION_REFRESH_MS
}

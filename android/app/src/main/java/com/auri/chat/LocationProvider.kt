package com.auri.chat

import android.annotation.SuppressLint
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Looper
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

class LocationProvider(private val context: Context) {
    private val locationManager =
        context.getSystemService(Context.LOCATION_SERVICE) as LocationManager

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_FINE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.ACCESS_COARSE_LOCATION,
            ) == PackageManager.PERMISSION_GRANTED

    suspend fun currentLocation(): Location? {
        if (!hasPermission()) return null
        lastKnownLocation()?.takeIf {
            System.currentTimeMillis() - it.time <= FRESH_LOCATION_MS
        }?.let { return it }
        return requestFreshLocation()
    }

    suspend fun freshLocation(): Location? {
        if (!hasPermission()) return null
        lastKnownLocation()?.takeIf {
            System.currentTimeMillis() - it.time <= RECENT_LOCATION_MS
        }?.let { return it }
        return requestFreshLocation(SEND_LOCATION_TIMEOUT_MS)
            ?: lastKnownLocation()
    }

    @SuppressLint("MissingPermission")
    private fun lastKnownLocation(): Location? {
        val candidates = listOfNotNull(
            runCatching {
                locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER)
            }.getOrNull(),
            runCatching {
                locationManager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
            }.getOrNull(),
        )
        return candidates.maxByOrNull { it.time }
    }

    @SuppressLint("MissingPermission")
    private suspend fun requestFreshLocation(
        timeoutMs: Long = LOCATION_TIMEOUT_MS,
    ): Location? =
        withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { continuation ->
                val providers = listOf(
                    LocationManager.GPS_PROVIDER,
                    LocationManager.NETWORK_PROVIDER,
                ).filter { provider ->
                    runCatching { locationManager.isProviderEnabled(provider) }
                        .getOrDefault(false)
                }

                if (providers.isEmpty()) {
                    continuation.resume(null)
                    return@suspendCancellableCoroutine
                }

                val finished = AtomicBoolean(false)
                val listener = object : LocationListener {
                    override fun onLocationChanged(location: Location) {
                        if (finished.compareAndSet(false, true)) {
                            providers.forEach {
                                runCatching { locationManager.removeUpdates(this) }
                            }
                            continuation.resume(location)
                        }
                    }

                    override fun onProviderDisabled(provider: String) = Unit

                    override fun onProviderEnabled(provider: String) = Unit

                    override fun onStatusChanged(
                        provider: String?,
                        status: Int,
                        extras: Bundle?,
                    ) = Unit
                }

                continuation.invokeOnCancellation {
                    if (finished.compareAndSet(false, true)) {
                        providers.forEach {
                            runCatching { locationManager.removeUpdates(listener) }
                        }
                    }
                }

                providers.forEach { provider ->
                    runCatching {
                        locationManager.requestLocationUpdates(
                            provider,
                            MIN_UPDATE_INTERVAL_MS,
                            MIN_UPDATE_DISTANCE_METERS,
                            listener,
                            Looper.getMainLooper(),
                        )
                    }
                }
            }
        }

    companion object {
        private const val FRESH_LOCATION_MS = 5 * 60_000L
        private const val RECENT_LOCATION_MS = 30_000L
        private const val LOCATION_TIMEOUT_MS = 12_000L
        private const val SEND_LOCATION_TIMEOUT_MS = 6_000L
        private const val MIN_UPDATE_INTERVAL_MS = 0L
        private const val MIN_UPDATE_DISTANCE_METERS = 0f
    }
}

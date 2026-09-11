package com.auri.chat

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceRegistrationPolicyTest {
    @Test
    fun `refreshes when registration id changes`() {
        assertTrue(shouldRefreshDeviceRegistration("new", "old", 100L, 200L))
    }

    @Test
    fun `does not refresh a recent matching registration`() {
        assertFalse(
            shouldRefreshDeviceRegistration(
                registrationId = "same",
                lastRegisteredId = "same",
                lastRegisteredAt = 1_000L,
                now = 1_000L + DEVICE_REGISTRATION_REFRESH_MS - 1L,
            ),
        )
    }

    @Test
    fun `refreshes a matching registration after one day`() {
        assertTrue(
            shouldRefreshDeviceRegistration(
                registrationId = "same",
                lastRegisteredId = "same",
                lastRegisteredAt = 1_000L,
                now = 1_000L + DEVICE_REGISTRATION_REFRESH_MS,
            ),
        )
    }

    @Test
    fun `refreshes after clock rollback instead of suppressing forever`() {
        assertTrue(shouldRefreshDeviceRegistration("same", "same", 2_000L, 1_000L))
    }
}

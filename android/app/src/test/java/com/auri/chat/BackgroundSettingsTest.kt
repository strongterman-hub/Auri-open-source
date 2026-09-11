package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BackgroundSettingsTest {
    @Test
    fun `maps common Chinese Android brands to tailored guidance`() {
        assertEquals("xiaomi", backgroundSettingsProfile("Xiaomi", "Redmi")?.family)
        assertEquals("oppo", backgroundSettingsProfile("OPPO", "OPPO")?.family)
        assertEquals("oppo", backgroundSettingsProfile("realme", "realme")?.family)
        assertEquals("vivo", backgroundSettingsProfile("vivo", "iQOO")?.family)
        assertEquals("huawei", backgroundSettingsProfile("HUAWEI", "HUAWEI")?.family)
        assertEquals("honor", backgroundSettingsProfile("HONOR", "HONOR")?.family)
    }

    @Test
    fun `does not expose a guessed autostart entry for generic Android`() {
        assertNull(backgroundSettingsProfile("Google", "google"))
    }

    @Test
    fun `oppo wording does not pretend the setting is named autostart`() {
        val profile = backgroundSettingsProfile("OPPO", "OPPO")!!
        assertEquals("后台运行与耗电管理", profile.title)
    }
}

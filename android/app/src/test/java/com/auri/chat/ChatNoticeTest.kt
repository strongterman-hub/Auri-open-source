package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChatNoticeTest {
    @Test
    fun jsonNullTextIsNotDisplayedAsNotice() {
        assertNull(normalizeOptionalNotice(null))
        assertNull(normalizeOptionalNotice("null"))
        assertNull(normalizeOptionalNotice("  NULL  "))
        assertNull(normalizeOptionalNotice("   "))
    }

    @Test
    fun realResetNoticeIsPreserved() {
        assertEquals(
            "上次会话已过期，已为你开启新会话。",
            normalizeOptionalNotice("  上次会话已过期，已为你开启新会话。  "),
        )
    }
}

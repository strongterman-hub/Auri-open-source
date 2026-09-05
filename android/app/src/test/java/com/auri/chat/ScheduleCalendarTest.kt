package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.YearMonth

class ScheduleCalendarTest {
    @Test
    fun monthCellsBeginOnMondayAndContainEveryDay() {
        val cells = monthCalendarCells(YearMonth.of(2026, 9))
        assertEquals(LocalDate.of(2026, 9, 1), cells[1])
        assertEquals(30, cells.count { it != null })
        assertEquals(0, cells.size % 7)
    }

    @Test
    fun crossMidnightEventAppearsOnBothDatesButMidnightEndIsExclusive() {
        val event = ScheduleEvent(
            id = "one", title = "夜间行程",
            startsAt = OffsetDateTime.parse("2026-09-07T23:30:00+08:00"),
            endsAt = OffsetDateTime.parse("2026-09-09T00:00:00+08:00"),
            timezone = "Asia/Shanghai", allDay = false, location = "", notes = "",
            reminderMinutes = null, repeat = "none", repeatUntil = null, status = "scheduled",
            version = 1, occurrenceDate = LocalDate.of(2026, 9, 7), isException = false,
        )
        assertTrue(eventCoversDate(event, LocalDate.of(2026, 9, 7)))
        assertTrue(eventCoversDate(event, LocalDate.of(2026, 9, 8)))
        assertFalse(eventCoversDate(event, LocalDate.of(2026, 9, 9)))
    }
}

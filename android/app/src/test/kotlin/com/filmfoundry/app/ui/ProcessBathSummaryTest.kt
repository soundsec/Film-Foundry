package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessBathSummaryTest {
    @Test fun inheritedValuesAndLocalOverridesStaySeparateWithoutChangingDraft() {
        val shared=mapOf("time_min" to "8.0","temperature_c" to "20.0","developer_type" to "standard")
        val step=mapOf("action" to "develop_silver","time_min" to 10.5,"temperature_c" to 24.0)
        val values=ProcessBathSummary.resolve(step,shared).associateBy { it.key }
        assertEquals(10.5,values.getValue("time_min").value);assertTrue(values.getValue("temperature_c").overridden)
        assertEquals("standard",values.getValue("developer_type").value);assertFalse(values.getValue("developer_type").overridden)
        assertFalse(values.containsKey("action"));assertEquals("8.0",shared["time_min"])
    }
    @Test fun unfinishedInputAndAdditionalBathConditionsAreNotHidden() {
        val values=ProcessBathSummary.resolve(mapOf("label" to "test","agitation" to 0.0),mapOf("time_min" to "-"))
        assertEquals("-",values.first { it.key=="time_min" }.value)
        assertTrue(values.single { it.key=="agitation" }.overridden)
        assertFalse(values.any { it.key=="label" })
    }
}

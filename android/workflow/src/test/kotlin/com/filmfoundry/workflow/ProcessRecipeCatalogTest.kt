package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessRecipeCatalogTest {
    @Test fun processDefinitionsDetachAndFailedWritesLeaveTheCatalogUnchanged() {
        val memory=MemoryRecipeCatalogStore();var fail=false
        val store=object: RecipeCatalogStore by memory { override fun writeCatalog(bytes: ByteArray) { if(fail) throw java.io.IOException("failed");memory.writeCatalog(bytes) } }
        val catalog=ProcessRecipeCatalog(store);val recipe=mutableMapOf<String,Any?>("time_min" to 4.0,"chemistry" to "cross")
        catalog.save("process","First",recipe);recipe["time_min"]=20.0
        assertEquals(4.0,catalog.list().single().recipe["time_min"])
        assertEquals("cross",ProcessRecipeCatalog(store).list().single().recipe["chemistry"])
        fail=true
        assertThrows(java.io.IOException::class.java) { catalog.save("process","New",mapOf("time_min" to 8.0)) }
        assertThrows(java.io.IOException::class.java) { catalog.remove("process") }
        assertEquals("First",catalog.list().single().label)
        fail=false;catalog.remove("process");assertTrue(ProcessRecipeCatalog(store).list().isEmpty())
    }
}

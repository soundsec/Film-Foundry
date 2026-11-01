package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class LibrarySelectionTest {
    @Test fun selectionUsesStableIdsAcrossPagesAndCancelClearsTheWholeScope() {
        data class Item(val id: String,val label: String)
        val state=LibrarySelection<Item> { it.id }
        state.start();assertTrue(state.active);assertEquals(0,state.count)
        state.toggle(Item("a","page 1"));state.toggle(Item("b","page 2"))
        assertEquals(listOf("a","b"),state.values.map { it.id })
        state.selectAll(listOf(Item("b","updated"),Item("c","page 3")))
        assertEquals(3,state.count);assertEquals("updated",state.values[1].label)
        state.toggle(Item("a","a new object"));assertEquals(listOf("b","c"),state.values.map { it.id })
        state.clear();assertFalse(state.active);assertEquals(0,state.count)
    }
}

package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class RecipeJsonEquivalenceTest {
    @Test fun encoderNumberRepresentationsDoNotRelaxActualFrozenParameterChecks() {
        assertTrue(RecipeJson.equivalent(mapOf("film" to listOf(1,.025,0)),mapOf("film" to listOf(1.0,.025,0.0))))
        assertFalse(RecipeJson.equivalent(mapOf("film" to listOf(1,.025)),mapOf("film" to listOf(1,.026))))
        assertFalse(RecipeJson.equivalent(listOf(1,2),listOf(2,1)))
        assertFalse(RecipeJson.equivalent(mapOf("a" to null),emptyMap<String,Any?>()))
        assertFalse(RecipeJson.equivalent("1",1))
        assertFalse(RecipeJson.equivalent(9007199254740993L,9007199254740992.0))
        assertFalse(RecipeJson.equivalent(Double.NaN,Double.NaN))
    }
}

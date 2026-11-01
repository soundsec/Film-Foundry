package com.filmfoundry.workflow

import com.filmfoundry.interchange.RecipeShareCodec

/** One small, detached decode per editor value; malformed input never reuses an older recipe. */
class RecipeInput {
    private var text: String?=null
    private var value: Map<String,Any?> = emptyMap()
    fun read(input: String): Map<String,Any?> {
        val next=input.trim()
        if(next==text) return value
        val parsed=if(next.isEmpty()) emptyMap() else frozenRecipe(RecipeShareCodec.decode(next))
        text=next;value=parsed
        return parsed
    }
}

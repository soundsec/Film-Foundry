package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeJson
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.File

class SeasonalPuzzleTest {
    @Test fun sharedManifestUsesStableIdentityCodecAndReplaceablePhrase() {
        val resources=FinderResources(File("src/main/assets/finder_resources.bin").readBytes())
        val definition=resources.section("collection")
        val root=RecipeJson.objectMap(definition.toString(Charsets.UTF_8))
        val puzzle=SeasonalPuzzle(definition);val identity="012345ABCDEF"
        val cipher=SeasonalPuzzle.unhex(puzzle.cipher(identity));val id=identity.toByteArray()
        val decoded=ByteArray(cipher.size) { (cipher[it].toInt() xor id[it%id.size].toInt()).toByte() }.toString(Charsets.UTF_8)
        assertTrue(puzzle.matches(decoded));assertFalse(puzzle.matches("wrong"))
        assertEquals(puzzle.cipher(identity),puzzle.cipher(identity))
        assertNotEquals(puzzle.cipher(identity),puzzle.cipher("FEDCBA543210"))
        val phrase="another season".toByteArray();val mask="FilmFoundry:edge".toByteArray()
        val replacement=SeasonalPuzzle.hex(ByteArray(phrase.size) { (phrase[it].toInt() xor mask[it%mask.size].toInt()).toByte() })
        val changed=SeasonalPuzzle(RecipeJson.text(root+("sealed_phrase" to replacement)).toByteArray())
        assertTrue(changed.matches("another season"));assertFalse(changed.matches(decoded))
    }
}

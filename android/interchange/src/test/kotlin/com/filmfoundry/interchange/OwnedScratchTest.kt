package com.filmfoundry.interchange

import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.util.UUID

class OwnedScratchTest {
    @TempDir lateinit var root: File
    @Test fun liveLeaseIsSkippedAndCloseOnlyRemovesItsWorkspace() {
        val original=File(root,"original.npz").apply { writeText("retain") }
        val lease=OwnedScratch.create(root);File(lease.directory,"rows.f32").writeText("temporary")
        assertEquals(OwnedScratch.Recovery(0,1,0),OwnedScratch.recover(root))
        assertTrue(lease.directory.exists());lease.close();lease.close()
        assertEquals(listOf(original),root.listFiles()!!.toList())
    }
    @Test fun abandonedOwnedWorkspaceIsReclaimedButUnknownCachesSurvive() {
        val abandoned=File(root,"film-owned-${UUID.randomUUID()}").apply { mkdir() }
        File(abandoned,"owner.lock").writeText(OwnedScratch.MARKER)
        File(abandoned,"data/deep").apply { mkdirs();File(this,"rows.f32").writeText("temporary") }
        val old=File(root,"film-rows-old").apply { mkdir();File(this,"data").writeText("unmarked") }
        val foreign=File(root,"film-owned-${UUID.randomUUID()}").apply { mkdir();File(this,"owner.lock").writeText("foreign") }
        assertEquals(OwnedScratch.Recovery(1,0,0),OwnedScratch.recover(root))
        assertFalse(abandoned.exists());assertTrue(old.exists());assertTrue(foreign.exists())
    }
}

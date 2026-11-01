package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FinderQuickSessionTest {
    @Test fun quickDoubleTapDoesNotRevealAndHoldOrAxisSlideConsumesResetCandidate() {
        val taps=FinderButtonTaps(300,8f,32f);val owner=Any()
        taps.down(owner,0,50f,100f);assertFalse(taps.shouldReveal(70,50f,100f,false));assertFalse(taps.up(80,50f,100f))
        taps.down(owner,120,50f,100f);assertFalse(taps.shouldReveal(160,50f,100f,false));assertTrue(taps.up(170,50f,100f))
        taps.down(owner,200,50f,100f)
        assertFalse(taps.shouldReveal(250,80f,100f,false)) // Side rail ignores horizontal movement.
        assertTrue(taps.shouldReveal(250,50f,89f,false));taps.revealed();assertFalse(taps.up(260,50f,100f))
        taps.down(owner,300,50f,100f);assertFalse(taps.shouldReveal(479,50f,100f,true))
        assertTrue(taps.shouldReveal(480,50f,100f,true));taps.revealed();assertFalse(taps.up(490,50f,100f))
        taps.down(owner,520,50f,100f);assertTrue(taps.shouldReveal(530,61f,100f,true))
        taps.cancel();assertFalse(taps.shouldReveal(900,61f,100f,true))
    }
    @Test fun doubleTapResetsOnlyAfterSecondReleaseOnTheSameButtonAndConsumesThePair() {
        val taps=FinderButtonTaps(300,8f,32f);val ev=Any();val zoom=Any()
        taps.down(ev,0,48f,98f);assertFalse(taps.up(50,48f,98f))
        taps.down(ev,120,49f,100f);assertTrue(taps.up(170,49f,100f))
        taps.down(ev,220,48f,98f);assertFalse(taps.up(250,48f,98f)) // Triple tap does not repeat reset.
        taps.down(zoom,280,404f,330f);assertFalse(taps.up(300,404f,330f))
        taps.down(ev,330,48f,98f);assertFalse(taps.up(350,48f,98f)) // Another control breaks the pair.
    }
    @Test fun draggingEitherTapHoldingCancellingOrLeavingTheControlCannotReset() {
        val taps=FinderButtonTaps(300,8f,32f);val owner=Any()
        taps.down(owner,0,0f,0f);taps.move(20f,0f);assertFalse(taps.up(60,0f,0f)) // Even a perpendicular out-and-back gesture is not a tap.
        taps.down(owner,100,0f,0f);assertFalse(taps.up(140,0f,0f))
        taps.down(owner,200,0f,0f);taps.move(0f,24f);assertFalse(taps.up(250,0f,24f)) // Second press-and-slide never resets.
        taps.down(owner,300,0f,0f);assertFalse(taps.up(700,0f,0f))
        taps.down(owner,750,0f,0f);assertFalse(taps.up(800,0f,0f,true))
        taps.down(owner,850,0f,0f);assertFalse(taps.up(900,0f,0f))
        taps.cancel();taps.down(owner,950,0f,0f);assertFalse(taps.up(1000,0f,0f))
        taps.down(owner,1100,100f,0f);assertFalse(taps.up(1150,100f,0f)) // Outside the pair's spatial tolerance.
        taps.down(owner,1500,100f,0f);assertFalse(taps.up(1550,100f,0f)) // Outside the platform time window.
    }
    @Test fun onePressSlidesAlongTheRailAxisWithoutOriginJumpOrReleaseChange() {
        val vertical=FinderDial(-12,12,3);val side=FinderDialGesture(vertical,false)
        side.begin(48f,98f);assertEquals(3,vertical.value)
        assertFalse(side.move(200f,98f,24f)) // Moving into the revealed fan does not itself change EV.
        assertTrue(side.move(200f,50f,24f));assertEquals(5,vertical.value)
        assertTrue(side.move(10f,122f,24f));assertEquals(2,vertical.value)
        side.finish();assertEquals(2,vertical.value);assertEquals(0f,side.fraction)
        val zoom=FinderDial(0,90,10);val bottom=FinderDialGesture(zoom,true)
        bottom.begin(404f,330f)
        assertFalse(bottom.move(404f,190f,24f)) // Entering the upward fan must not zoom.
        assertTrue(bottom.move(452f,190f,24f));assertEquals(12,zoom.value)
        assertTrue(bottom.move(380f,345f,24f));assertEquals(9,zoom.value)
        bottom.finish();assertEquals(9,zoom.value)
    }
    @Test fun replacementAndSameButtonPreventOldTimeoutFromClosingAnotherControl() {
        val editor=FinderQuickSession(3200)
        assertTrue(editor.toggle("ev",100));val old=editor.revision
        assertTrue(editor.toggle("zoom",200));val current=editor.revision
        assertFalse(editor.expire(old,5000));assertEquals("zoom",editor.key)
        assertFalse(editor.toggle("zoom",5001));assertNull(editor.key)
        assertTrue(editor.toggle("flash",5100));assertFalse(editor.expire(current,9000));assertEquals("flash",editor.key)
        editor.dismiss();assertNull(editor.key)
    }
    @Test fun longDragNeverTimesOutAndReleaseRestartsIdleWindow() {
        val editor=FinderQuickSession(3200);editor.toggle("ev",0);val revision=editor.revision
        editor.touch(100,true);assertFalse(editor.expire(revision,20_000))
        editor.touch(20_000,false);assertFalse(editor.expire(revision,23_199));assertTrue(editor.expire(revision,23_200));assertNull(editor.key)
    }
    @Test fun NativeStepsAccumulateSmallMovesClampAtEndpointsAndReverseWithoutOverflow() {
        val dial=FinderDial(-12,12,0);dial.start()
        for(distance in listOf(1f,3f,6f,10f)) assertFalse(dial.drag(distance,24f))
        assertTrue(dial.drag(13f,24f));assertEquals(1,dial.value)
        assertTrue(dial.drag(48f,24f));assertEquals(2,dial.value)
        assertTrue(dial.drag(1e12f,24f));assertEquals(12,dial.value)
        assertTrue(dial.drag(-1e12f,24f));assertEquals(-12,dial.value)
        dial.start();assertTrue(dial.drag(24f,24f));assertEquals(-11,dial.value)
        assertTrue(dial.select(0));assertFalse(dial.select(0))
    }
}

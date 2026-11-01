package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ViewerPageIdentityTest {
    @Test fun samePageUpdateRetainsTheCurrentDisplayUntilTheLatestReplacementCommits() {
        val pages=ViewerPageIdentity();val original=pages.request("negative:a");assertTrue(pages.commit(original))
        val update=pages.request("negative:a")
        assertEquals(original,pages.displayed)
        assertTrue(pages.preview(update));assertEquals(update,pages.pending)
        val latest=pages.request("negative:a");assertFalse(pages.commit(update))
        assertTrue(pages.preview(latest));assertTrue(pages.commit(latest));assertNull(pages.pending)
    }
    @Test fun explicitPreviewCanBecomeBrowsableBeforeTheFullReadAndOldUpgradesAreRejected() {
        val pages=ViewerPageIdentity();val a=pages.request("negative:a")
        assertTrue(pages.preview(a));assertEquals(a,pages.pending);assertTrue(pages.neighborsMatch(a))
        val b=pages.request("negative:b");assertFalse(pages.neighborsMatch(a))
        assertTrue(pages.preview(b));assertTrue(pages.neighborsMatch(b));assertFalse(pages.commit(a))
        assertTrue(pages.commit(b));assertNull(pages.pending)
    }
    @Test fun nextPageLoadingCannotPublishItsNeighborsOverTheOutgoingPage() {
        val pages=ViewerPageIdentity();val a=pages.request("photo:a");assertTrue(pages.commit(a))
        assertTrue(pages.neighborsMatch(a))
        val b=pages.request("photo:b")
        assertEquals(a,pages.displayed) // The outgoing strip remains A with B as its incoming thumbnail.
        assertFalse(pages.neighborsMatch(a));assertFalse(pages.neighborsMatch(b))
        assertTrue(pages.commit(b));assertTrue(pages.neighborsMatch(b));assertFalse(pages.neighborsMatch(a))
        val c=pages.request("photo:c");val d=pages.request("negative:d")
        assertFalse(pages.commit(c));assertEquals(b,pages.displayed)
        assertTrue(pages.commit(d));assertFalse(pages.neighborsMatch(c))
    }
    @Test fun returningToTheSameImageStillRejectsAnOlderPrefetchAndClearInvalidatesPendingDecode() {
        val pages=ViewerPageIdentity();val first=pages.request("photo:a");assertTrue(pages.commit(first))
        val second=pages.request("photo:a");assertTrue(pages.commit(second))
        assertFalse(pages.neighborsMatch(first));assertTrue(pages.neighborsMatch(second))
        val pending=pages.request("photo:b");pages.clear()
        assertFalse(pages.commit(pending));assertNull(pages.displayed);assertNull(pages.pending)
    }
}

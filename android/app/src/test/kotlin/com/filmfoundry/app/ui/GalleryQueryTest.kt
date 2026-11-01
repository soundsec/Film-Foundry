package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class GalleryQueryTest {
    @Test fun comparisonBoundariesIncludeUnratedAndComposeWithFavorites() {
        val expected=mapOf("gt" to listOf(4,5),"lt" to listOf(0,1,2),"ge" to listOf(3,4,5),"le" to listOf(0,1,2,3))
        for((relation,ratings) in expected) {
            val query=GalleryQuery(favoriteOnly=true,ratingRelation=relation,rating=3)
            assertEquals(ratings,(0..5).filter { query.accepts(it,true) });assertTrue((0..5).none { query.accepts(it,false) })
        }
    }
    @Test fun orderingUsesActualSizeNamesAndTimeWithStableTies() {
        val a=GalleryQuery.Item("a","B",20,100,0,false);val b=GalleryQuery.Item("b","A",10,500,4,true);val c=a.copy(id="c")
        val input=listOf(c,b,a)
        assertEquals(listOf("a","c","b"),GalleryQuery().ordered(input).map { it.id })
        assertEquals(listOf("b","a","c"),GalleryQuery(sort="size").ordered(input).map { it.id })
        assertEquals(listOf("b","a","c"),GalleryQuery(sort="name",descending=false).ordered(input).map { it.id })
        assertEquals(listOf("b"),GalleryQuery(favoriteOnly=true,ratingRelation="ge",rating=4).ordered(input).map { it.id })
    }
}

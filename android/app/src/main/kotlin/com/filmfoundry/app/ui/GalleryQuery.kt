package com.filmfoundry.app.ui

/** Catalog-only filtering and stable ordering, independent of image decoding. */
data class GalleryQuery(val sort: String="time",val descending: Boolean=true,
    val favoriteOnly: Boolean=false,val ratingRelation: String="any",val rating: Int=0) {
    data class Item(val id: String,val rollName: String,val time: Long,val bytes: Long,val stars: Int,val favorite: Boolean)
    fun accepts(stars: Int,favorite: Boolean)=(!favoriteOnly || favorite) && when(ratingRelation) {
        "gt"->stars>rating;"lt"->stars<rating;"ge"->stars>=rating;"le"->stars<=rating;else->true
    }
    fun ordered(items: List<Item>): List<Item> {
        val names=java.text.Collator.getInstance()
        return items.filter { accepts(it.stars,it.favorite) }.sortedWith { a,b ->
            val order=when(sort) { "size"->a.bytes.compareTo(b.bytes);"name"->names.compare(a.rollName,b.rollName);else->a.time.compareTo(b.time) }
            if(order==0) a.id.compareTo(b.id) else if(descending) -order else order
        }
    }
}

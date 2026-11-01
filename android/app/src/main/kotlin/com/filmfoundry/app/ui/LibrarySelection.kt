package com.filmfoundry.app.ui

/** Selection follows stable resource IDs across pages; it never owns or mutates resources. */
class LibrarySelection<T>(private val key: (T)->String) {
    var active=false;private set
    private val items=linkedMapOf<String,T>()
    val values get()=items.values.toList()
    val count get()=items.size
    fun contains(item: T)=key(item) in items
    fun start() { active=true }
    fun toggle(item: T) { active=true;val id=key(item);if(items.remove(id)==null) items[id]=item }
    fun selectAll(values: List<T>) { active=true;values.forEach { items[key(it)]=it } }
    fun clear() { items.clear();active=false }
}

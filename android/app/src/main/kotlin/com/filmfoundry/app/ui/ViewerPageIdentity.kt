package com.filmfoundry.app.ui

/** A page and its neighbors commit together; a pending image must not reindex the visible strip. */
class ViewerPageIdentity {
    data class Token(val generation: Long,val key: String)
    private var generation=0L
    var displayed: Token?=null;private set
    var pending: Token?=null;private set
    fun request(key: String): Token = Token(++generation,key).also { pending=it }
    fun preview(token: Token): Boolean {
        if(token!=pending) return false
        displayed=token;return true
    }
    fun commit(token: Token): Boolean {
        if(token!=pending) return false
        displayed=token;pending=null;return true
    }
    fun neighborsMatch(token: Token)=token==displayed && (pending==null || pending==displayed)
    fun clear() { generation++;displayed=null;pending=null }
}

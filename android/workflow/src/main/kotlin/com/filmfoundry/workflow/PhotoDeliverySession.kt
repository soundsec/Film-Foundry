package com.filmfoundry.workflow

/** Optional delivery follows material registration. A failed photo cannot invalidate a negative. */
class PhotoDeliverySession(existingResultIds: Set<String>,private val control: WorkflowControl) {
    private val delivered=existingResultIds.toMutableSet()
    var failed=0
        private set
    suspend fun deliver(resultId: String,write: suspend ()->Unit): Boolean {
        control.checkCancel()
        if(resultId in delivered) return true
        return try { write();delivered.add(resultId);true }
        catch(error: Exception) {
            control.checkCancel()
            if(error is WorkflowException && error.code == "cancelled") throw error
            failed++;false
        }
    }
}

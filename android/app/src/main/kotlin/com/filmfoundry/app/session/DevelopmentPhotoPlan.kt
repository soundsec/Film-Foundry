package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.workflow.ObservationImplementation
import com.filmfoundry.workflow.WorkflowException
import com.filmfoundry.workflow.frozenRecipe
import java.util.concurrent.CancellationException

/** Optional scanning is prepared independently; a bad scanner draft cannot block development. */
class DevelopmentPhotoPlan private constructor(val recipe: Map<String,Any?>?,val share: String?,val model: String?,val error: String?) {
    val enabled get()=recipe!=null
    companion object {
        fun prepare(enabled: Boolean,resolve: ()->Map<String,Any?>): DevelopmentPhotoPlan {
            if(!enabled) return DevelopmentPhotoPlan(null,null,null,null)
            return try {
                val recipe=frozenRecipe(resolve())
                DevelopmentPhotoPlan(recipe,RecipeShareCodec.encode(recipe),ObservationImplementation.forRecipe(recipe),null)
            } catch(error: Exception) {
                if(error is CancellationException || error is WorkflowException && error.code=="cancelled") throw error
                DevelopmentPhotoPlan(null,null,null,(error.message ?: error.javaClass.simpleName).take(240))
            }
        }
    }
}

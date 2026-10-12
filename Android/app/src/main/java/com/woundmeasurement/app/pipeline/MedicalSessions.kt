package com.woundmeasurement.app.pipeline

import android.content.Context
import com.woundmeasurement.app.data.store.AppSettings

object MedicalSessions {
    @Volatile private var shared: MedicalSessionPool<BackendClient>? = null
    /** Call on IO dispatcher. Does not retain an Activity or persist tokens. */
    fun acquire(context: Context): BackendClient {
        val app=context.applicationContext
        val pool=shared ?: synchronized(this) {
            shared ?: MedicalSessionPool(input = {
                MedicalSessionPool.Input(AppSettings.backendUrl(app),AppSettings.backendUser(app),
                    AppSettings.backendPassword(app),MedicalSessionEpoch.snapshot())
            }, factory = { BackendClient(it) }).also { shared=it }
        }
        return pool.acquire()
    }
}

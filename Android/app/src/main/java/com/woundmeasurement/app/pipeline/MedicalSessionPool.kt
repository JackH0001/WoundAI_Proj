package com.woundmeasurement.app.pipeline

import java.io.IOException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.atomic.AtomicLong

/** Process memory only; changing credentials or Google entry invalidates old clients. */
object MedicalSessionEpoch {
    private val generation = AtomicLong()
    fun snapshot(): Long = generation.get()
    fun invalidate() { generation.incrementAndGet() }
}

interface MedicalSessionClient {
    val identity: LoginIdentity?
    val hasSessionToken: Boolean
    fun login(username: String, password: String): Boolean
    fun validateMedicalSession(): LoginIdentity
}

/** Blocking IO acquisition. Network calls run outside the monitor; concurrent callers share a task. */
class MedicalSessionPool<C : MedicalSessionClient>(private val input: () -> Input,
                                                   private val factory: (String) -> C) {
    data class Binding(val origin: String, val user: String, val epoch: Long)
    class Input(val origin: String, val user: String, val password: String, val epoch: Long) {
        val binding get() = Binding(origin.trimEnd('/'), user, epoch)
    }
    data class Verified<C>(val client: C, val identity: LoginIdentity)
    private class Pending<C>(val binding: Binding, val future: CompletableFuture<Verified<C>>)
    private val monitor = Any()
    private var cached: Pair<Binding,C>? = null
    private var pending: Pending<C>? = null
    fun acquire(): C = acquireVerified().client

    /** Immutable receipt of this acquisition; another refresh may clear the shared client identity. */
    fun acquireVerified(): Verified<C> {
        val current = input(); val binding = current.binding
        if (current.origin.isBlank() || current.user.isBlank() || current.password.isBlank())
            throw IOException("尚未設定 WoundAI 帳號密碼")
        var owner = false
        val active = synchronized(monitor) {
            pending?.takeIf { it.binding == binding } ?: Pending<C>(binding, CompletableFuture()).also {
                pending = it; owner = true
            }
        }
        if (owner) {
            try {
                val candidate = synchronized(monitor) { cached?.takeIf { it.first == binding }?.second }
                    ?: factory(binding.origin)
                if (!candidate.hasSessionToken && !candidate.login(current.user, current.password))
                    throw IOException("WoundAI 登入未通過")
                val identity = candidate.validateMedicalSession()
                if (identity.identity != current.user && identity.user != current.user)
                    throw IOException("登入身分與設定帳號不一致")
                synchronized(monitor) {
                    if (input().binding != binding) throw IOException("登入設定已變更，請重新驗證")
                    if (pending === active) { cached = binding to candidate; pending = null }
                }
                active.future.complete(Verified(candidate, identity))
            } catch (error: Exception) {
                synchronized(monitor) { if (pending === active) pending = null }
                active.future.completeExceptionally(error)
            }
        }
        val result = try { active.future.get() }
        catch (error: ExecutionException) { throw (error.cause as? Exception ?: IOException("登入驗證失敗")) }
        catch (error: InterruptedException) { Thread.currentThread().interrupt(); throw error }
        if (input().binding != binding) throw IOException("登入設定已變更，請重新驗證")
        return result
    }
}

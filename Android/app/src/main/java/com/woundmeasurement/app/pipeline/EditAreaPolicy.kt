package com.woundmeasurement.app.pipeline

import kotlin.math.abs
import kotlin.math.round

/** Area identity uses this session's entry boundary and scale, not AI IoU or tissue colours. */
object EditAreaPolicy {
    fun sameBoundaryAndScale(a: EditRaster, b: EditRaster): Boolean {
        if (a.mw <= 0 || a.mh <= 0 || b.mw <= 0 || b.mh <= 0 ||
            a.mask.size != a.mw * a.mh || b.mask.size != b.mw * b.mh ||
            a.canvasW != b.canvasW || a.canvasH != b.canvasH ||
            a.mScale <= 0 || a.mScale != b.mScale || a.cm2PerPx != b.cm2PerPx) return false
        if (a.rx0 == b.rx0 && a.ry0 == b.ry0 && a.mw == b.mw && a.mh == b.mh) return a.mask.contentEquals(b.mask)
        val dx = (a.rx0 - b.rx0).toDouble() * a.mScale
        val dy = (a.ry0 - b.ry0).toDouble() * a.mScale
        if (!dx.isFinite() || !dy.isFinite() || abs(dx) > a.mw + b.mw || abs(dy) > a.mh + b.mh ||
            abs(dx - round(dx)) >= 0.0001 || abs(dy - round(dy)) >= 0.0001) return false
        val ox = round(dx).toInt(); val oy = round(dy).toInt()
        if (a.mask.count { it.toInt() != 0 } != b.mask.count { it.toInt() != 0 }) return false
        for (i in a.mask.indices) if (a.mask[i].toInt() != 0) {
            val x = i % a.mw + ox; val y = i / a.mw + oy
            if (x < 0 || y < 0 || x >= b.mw || y >= b.mh || b.mask[y * b.mw + x].toInt() == 0) return false
        }
        return true
    }
    fun area(original: Double?, entry: EditRaster, current: EditRaster): Double? {
        if (original != null && sameBoundaryAndScale(entry, current)) return original
        return current.cm2PerPx?.takeIf { it > 0 && it.isFinite() }?.let { c -> current.mask.count { it.toInt() != 0 } * c }
    }
}

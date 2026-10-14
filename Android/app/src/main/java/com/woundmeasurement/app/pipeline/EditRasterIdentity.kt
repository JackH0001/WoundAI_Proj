package com.woundmeasurement.app.pipeline

/** Exact identity, never an IoU tolerance: even one altered pixel is a revision. */
object EditRasterIdentity {
    fun sameContent(a: EditRaster, b: EditRaster): Boolean =
        a.mw == b.mw && a.mh == b.mh && a.rx0 == b.rx0 && a.ry0 == b.ry0 &&
        a.mScale == b.mScale && a.cm2PerPx == b.cm2PerPx &&
        a.canvasW == b.canvasW && a.canvasH == b.canvasH &&
        a.mask.contentEquals(b.mask) && a.tissue.contentEquals(b.tissue) &&
        a.origMask.contentEquals(b.origMask) &&
        ((a.wbGains == null && b.wbGains == null) ||
            (a.wbGains != null && b.wbGains != null && a.wbGains.contentEquals(b.wbGains)))
}

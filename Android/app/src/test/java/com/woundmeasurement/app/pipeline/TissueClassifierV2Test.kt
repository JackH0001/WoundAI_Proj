package com.woundmeasurement.app.pipeline

import org.junit.Assert.assertEquals
import org.junit.Test

/** 對齊 SSOT 組織金標(engineering/generated/tissue_golden.json)；三端分類須一致。 */
class TissueClassifierV2Test {
    @Test fun goldenSamples() {
        // rgb -> 期望組織碼(1壞死/2腐肉/3肉芽/4上皮/5其他)
        val cases = listOf(
            Triple(intArrayOf(105, 22, 30), 3, "暗紅墨水→肉芽"),
            Triple(intArrayOf(35, 33, 30), 1, "暗低飽和→壞死"),
            Triple(intArrayOf(200, 170, 40), 2, "黃→腐肉"),
            Triple(intArrayOf(235, 200, 205), 4, "淡粉→上皮"),
            Triple(intArrayOf(190, 40, 45), 3, "紅→肉芽"),
            Triple(intArrayOf(150, 150, 150), 5, "灰→其他"),
            Triple(intArrayOf(60, 55, 50), 1, "暗灰→壞死")
        )
        for ((rgb, expected, _) in cases)
            assertEquals(expected, TissueClassifierV2.classifyPixel(rgb[0], rgb[1], rgb[2]))
    }
    @Test fun hsvMatchesOpenCV() {
        // (200,170,40) → 黃,H≈24,S≈204,V=200
        val hsv = TissueClassifierV2.rgb2hsv(200, 170, 40)
        assertEquals(200, hsv.v); assertEquals(true, hsv.h in 22..26); assertEquals(true, hsv.s in 200..206)
    }
}

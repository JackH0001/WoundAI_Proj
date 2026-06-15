package com.woundmeasurement.app.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey
import java.util.Date

@Entity(tableName = "patients")
data class PatientEntity(
    @PrimaryKey
    val id: String,
    val name: String,
    val birthDate: String,
    val gender: String,
    val medicalRecordNumber: String,
    val department: String,
    val registrationTime: Date,
    val lastVisitTime: Date? = null,
    val notes: String? = null
) 
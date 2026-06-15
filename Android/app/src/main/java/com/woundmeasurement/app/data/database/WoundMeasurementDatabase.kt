package com.woundmeasurement.app.data.database

import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters
import android.content.Context
import com.woundmeasurement.app.data.dao.PatientDao
import com.woundmeasurement.app.data.dao.MeasurementDao
import com.woundmeasurement.app.data.entity.PatientEntity
import com.woundmeasurement.app.data.entity.MeasurementEntity
import com.woundmeasurement.app.data.converter.DateConverter

@Database(
    entities = [
        PatientEntity::class,
        MeasurementEntity::class
    ],
    version = 1,
    exportSchema = false
)
@TypeConverters(DateConverter::class)
abstract class WoundMeasurementDatabase : RoomDatabase() {
    
    abstract fun patientDao(): PatientDao
    abstract fun measurementDao(): MeasurementDao
    
    companion object {
        @Volatile
        private var INSTANCE: WoundMeasurementDatabase? = null
        
        fun getDatabase(context: Context): WoundMeasurementDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    WoundMeasurementDatabase::class.java,
                    "wound_measurement_database"
                )
                .fallbackToDestructiveMigration()
                .build()
                INSTANCE = instance
                instance
            }
        }
    }
} 
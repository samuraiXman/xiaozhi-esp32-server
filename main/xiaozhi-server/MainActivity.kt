package com.example.yourpackage

import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    
    private lateinit var processFlow: ProcessFlowView
    private var currentStep = 0
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        
        processFlow = findViewById(R.id.processFlow)
        
        // Add items to the process flow
        processFlow.addItem("客户签约", R.drawable.ic_customer_sign)
        processFlow.addItem("扫描税单", R.drawable.ic_tax_form)
        processFlow.addItem("预授权冻结", R.drawable.ic_authorization)
        processFlow.addItem("给客户预付款", R.drawable.ic_payment)
        
        // Set the initial selected position
        processFlow.setSelectedPosition(currentStep)
        
        // Set up the next button
        findViewById<Button>(R.id.btnNext).setOnClickListener {
            if (currentStep < 3) { // 4 items, so max index is 3
                currentStep++
                processFlow.setSelectedPosition(currentStep)
            }
        }
    }
} 
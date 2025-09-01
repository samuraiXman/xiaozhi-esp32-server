import android.content.Context
import android.graphics.Color
import android.util.AttributeSet
import android.view.Gravity
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Represents an individual item in the ProcessFlowView
 * Contains an icon and text with selected/unselected states
 */
class ProcessFlowItemView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : LinearLayout(context, attrs, defStyleAttr) {

    private val imageView: ImageView
    private val textView: TextView
    
    // Default colors
    private var selectedColor = Color.RED
    private var unselectedColor = Color.GRAY
    private val selectedTextColor = Color.BLACK
    private val unselectedTextColor = Color.DKGRAY

    init {
        // Set up the layout
        orientation = VERTICAL
        gravity = Gravity.CENTER
        
        // Add padding
        val padding = (8 * context.resources.displayMetrics.density).toInt()
        setPadding(padding, padding, padding, padding)
        
        // Create and add ImageView
        imageView = ImageView(context).apply {
            layoutParams = LayoutParams(
                LayoutParams.WRAP_CONTENT,
                LayoutParams.WRAP_CONTENT
            )
        }
        addView(imageView)
        
        // Create and add TextView
        textView = TextView(context).apply {
            layoutParams = LayoutParams(
                LayoutParams.WRAP_CONTENT,
                LayoutParams.WRAP_CONTENT
            )
            gravity = Gravity.CENTER
            setPadding(0, padding / 2, 0, 0)
        }
        addView(textView)
        
        // Default state is unselected
        updateAppearance(false)
    }
    
    /**
     * Set custom colors for selected and unselected states
     */
    fun setColors(selectedColor: Int, unselectedColor: Int) {
        this.selectedColor = selectedColor
        this.unselectedColor = unselectedColor
        updateAppearance(isSelected)
    }

    /**
     * Set the title text for this item
     */
    fun setTitle(title: String) {
        textView.text = title
    }

    /**
     * Set the icon for this item
     */
    fun setIcon(iconResId: Int) {
        imageView.setImageResource(iconResId)
    }

    /**
     * Set whether this item is selected
     */
    fun setSelected(selected: Boolean) {
        super.setSelected(selected)
        updateAppearance(selected)
    }
    
    /**
     * Update the visual appearance based on selection state
     */
    private fun updateAppearance(selected: Boolean) {
        if (selected) {
            imageView.setColorFilter(selectedColor)
            textView.setTextColor(selectedTextColor)
        } else {
            imageView.setColorFilter(unselectedColor)
            textView.setTextColor(unselectedTextColor)
        }
        
        invalidate()
    }
} 
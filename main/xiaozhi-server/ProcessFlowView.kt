import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.core.content.withStyledAttributes
import androidx.core.view.children
import kotlin.math.max

/**
 * A custom ViewGroup that displays a series of steps with arrow connections between them.
 * Each step contains an icon and text, with selected and unselected states.
 */
class ProcessFlowView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : ViewGroup(context, attrs, defStyleAttr) {
    
    // Arrow connection image between items
    private val arrowConnectors = mutableListOf<ImageView>()
    
    // Default values
    private var itemSpacing = (20 * context.resources.displayMetrics.density).toInt() // spacing between items in dp
    private var selectedColor = Color.RED
    private var unselectedColor = Color.LTGRAY
    private var arrowDrawableResId = android.R.drawable.arrow_right_float // default arrow drawable
    
    // Keep track of selected position
    private var selectedPosition = 0

    init {
        // Make sure this ViewGroup will draw even with empty background
        setWillNotDraw(false)
        
        // Load attributes from XML
        context.withStyledAttributes(attrs, R.styleable.ProcessFlowView) {
            itemSpacing = getDimensionPixelSize(
                R.styleable.ProcessFlowView_pfv_itemSpacing,
                itemSpacing
            )
            selectedColor = getColor(
                R.styleable.ProcessFlowView_pfv_selectedColor,
                selectedColor
            )
            unselectedColor = getColor(
                R.styleable.ProcessFlowView_pfv_unselectedColor,
                unselectedColor
            )
            arrowDrawableResId = getResourceId(
                R.styleable.ProcessFlowView_pfv_arrowDrawable,
                arrowDrawableResId
            )
        }
    }

    /**
     * Add a new item to the process flow
     * @param title The text to display
     * @param iconResId The icon resource ID
     * @return true if the item was added, false if max items reached
     */
    fun addItem(title: String, iconResId: Int): Boolean {
        // Get current count of actual items (not arrows)
        val itemCount = getItemCount()
        
        // Check if we've reached max items
        if (itemCount >= 5) {
            return false
        }
        
        // Create new item view
        val itemView = ProcessFlowItemView(context).apply {
            setTitle(title)
            setIcon(iconResId)
            setSelected(itemCount == selectedPosition)
            setColors(selectedColor, unselectedColor)
            layoutParams = LayoutParams(
                LayoutParams.WRAP_CONTENT,
                LayoutParams.WRAP_CONTENT
            )
        }
        
        addView(itemView)
        
        // Add connector arrow if this isn't the first item
        if (itemCount > 0) {
            val arrowConnector = ImageView(context).apply {
                setImageResource(arrowDrawableResId)
                visibility = View.VISIBLE
            }
            arrowConnectors.add(arrowConnector)
            addView(arrowConnector)
        }
        
        requestLayout()
        return true
    }
    
    /**
     * Get the number of item views (not counting arrows)
     */
    private fun getItemCount(): Int {
        return children.filterIsInstance<ProcessFlowItemView>().count()
    }

    /**
     * Set the selected position
     */
    fun setSelectedPosition(position: Int) {
        if (position < 0 || position >= getItemCount()) {
            return
        }
        
        selectedPosition = position
        
        // Update all items
        children.filterIsInstance<ProcessFlowItemView>().forEachIndexed { index, view ->
            view.setSelected(index == position)
        }
        
        invalidate()
    }
    
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val itemViews = children.filterIsInstance<ProcessFlowItemView>().toList()
        val arrowViews = children.filterIsInstance<ImageView>().toList()
        
        if (itemViews.isEmpty()) {
            super.onMeasure(widthMeasureSpec, heightMeasureSpec)
            return
        }

        // Measure all items
        var totalWidth = 0
        var maxHeight = 0
        
        // Measure all item views first
        itemViews.forEach { itemView ->
            measureChild(itemView, widthMeasureSpec, heightMeasureSpec)
            totalWidth += itemView.measuredWidth
            maxHeight = max(maxHeight, itemView.measuredHeight)
        }
        
        // Measure arrow connectors
        arrowViews.forEach { arrowView ->
            measureChild(arrowView, widthMeasureSpec, heightMeasureSpec)
            totalWidth += arrowView.measuredWidth
        }
        
        // Account for spacing
        totalWidth += itemSpacing * (itemViews.size - 1)
        
        // Determine final width and height
        val width = resolveSize(totalWidth + paddingLeft + paddingRight, widthMeasureSpec)
        val height = resolveSize(maxHeight + paddingTop + paddingBottom, heightMeasureSpec)
        
        setMeasuredDimension(width, height)
    }

    override fun onLayout(changed: Boolean, left: Int, top: Int, right: Int, bottom: Int) {
        if (childCount == 0) return
        
        val itemViews = children.filterIsInstance<ProcessFlowItemView>().toList()
        val arrowViews = children.filterIsInstance<ImageView>().toList()
        
        var currentLeft = paddingLeft
        val centerVertical = (bottom - top - paddingTop - paddingBottom) / 2 + paddingTop
        
        // Layout item views and arrows
        itemViews.forEachIndexed { index, itemView ->
            // Layout the item
            val itemHeight = itemView.measuredHeight
            val itemTop = centerVertical - itemHeight / 2
            
            itemView.layout(
                currentLeft,
                itemTop,
                currentLeft + itemView.measuredWidth,
                itemTop + itemHeight
            )
            
            currentLeft += itemView.measuredWidth
            
            // If not the last item, layout an arrow
            if (index < arrowViews.size) {
                val arrowView = arrowViews[index]
                val arrowWidth = arrowView.measuredWidth
                val arrowHeight = arrowView.measuredHeight
                val arrowTop = centerVertical - arrowHeight / 2
                
                arrowView.layout(
                    currentLeft + itemSpacing / 2 - arrowWidth / 2,
                    arrowTop,
                    currentLeft + itemSpacing / 2 + arrowWidth / 2,
                    arrowTop + arrowHeight
                )
                
                currentLeft += itemSpacing
            }
        }
    }
    
    /**
     * Ensure we have between 2 and 5 items
     */
    override fun checkLayoutParams(p: LayoutParams?): Boolean {
        return p is LayoutParams
    }

    override fun generateDefaultLayoutParams(): LayoutParams {
        return LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT)
    }

    override fun generateLayoutParams(attrs: AttributeSet?): LayoutParams {
        return LayoutParams(context, attrs)
    }

    override fun generateLayoutParams(p: ViewGroup.LayoutParams?): LayoutParams {
        return LayoutParams(p)
    }
    
    class LayoutParams : ViewGroup.LayoutParams {
        constructor(c: Context, attrs: AttributeSet?) : super(c, attrs)
        constructor(width: Int, height: Int) : super(width, height)
        constructor(source: ViewGroup.LayoutParams?) : super(source)
    }
} 
"""
Draw interactive distribution plots that allow to identify particular obseservations.

One can think of the interactive distribution plot as a clickable histogram.
The main difference to a histogram is that in this type of plot
every bar is a stack of bricks where each brick identifies a particular observation.
By hovering or clicking on a particular brick you can learn more about that observation
making it easy to identify and analyze patterns.

Estimagic uses interactive distribution plots for two types of visualizations:
1. Parameter comparison plots
2. Loglikelihood contribution plots

"""
import warnings
from functools import partial

import numpy as np
import pandas as pd
from bokeh.layouts import gridplot
from bokeh.models import BoxSelectTool
from bokeh.models import CDSView
from bokeh.models import ColumnDataSource
from bokeh.models import HoverTool
from bokeh.models import IndexFilter
from bokeh.models import TapTool
from bokeh.models import Title
from bokeh.models.callbacks import CustomJS
from bokeh.models.filters import CustomJSFilter
from bokeh.models.widgets import CheckboxGroup
from bokeh.models.widgets import RangeSlider
from bokeh.plotting import figure
from bokeh.plotting import show

from estimagic.dashboard.plotting_functions import get_color_palette


def interactive_distribution_plot(
    df,
    value_col,
    id_col,
    group_cols,
    subgroup_col,
    lower_bound_col=None,
    upper_bound_col=None,
    height=None,
    width=500,
    x_padding=0.1,
    num_bins=50,
    axis_for_every_parameter=False,
):
    """Create an interactive distribution plot from a tidy DataFrame.

    Args:
        df (pd.DataFrame):
            Tidy DataFrame.
            see: http://vita.had.co.nz/papers/tidy-data.pdf
        value_col (str):
            Name of the column for which to draw the histogram.
            In case of a parameter comparison plot this would be the "value" column
            of the params DataFrame returned by maximize or minimize.
        id_col (str):
            Name of the column that identifies
            which values belong to the same observation.
            In case of a parameter comparison plot this would be the "model_name" column.
        group_cols (list):
            Name of the columns that identify groups that will be plotted together.
            In case of a parameter comparison plot this would be the parameter group
            and parameter name.
        subgroup_col (str, optional):
            Name of a column according to whose values individual bricks will be
            color coded. The selection which column is the subgroup_col
            can be changed in the plot from a dropdown menu.
        lower_bound_col (str, optional):
            Name of the column identifying the lower bound of the whisker.
        upper_bound_col (str, optional):
            Name of the column identifying the upper bound of the whisker.
        height (int, optional):
            height of the plot (in pixels).
        width (int, optional):
            width of the plot (in pixels).
        x_padding (float, optional):
            the x_range is extended on each side by this factor of the range of the data
        num_bins (int, optional):
            number of bins
        axis_for_every_parameter (bool, optional):
            if False the x axis is only shown once for every group of parameters.

    Returns:
        source (bokeh.models.ColumnDataSource): data underlying the plots
        gridplot (bokeh.layouts.Column): grid of the distribution plots.

    """

    if len(group_cols) != 2 or type(group_cols) != list:
        raise NotImplementedError("Only supporting two group columns at the moment.")

    hist_data = add_histogram_columns_to_tidy_df(
        df=df,
        group_cols=group_cols,
        value_col=value_col,
        subgroup_col=subgroup_col,
        id_col=id_col,
        num_bins=num_bins,
        x_padding=x_padding,
    )

    if height is None:
        height = _determine_plot_height(
            figure_height=height, data=hist_data, group_cols=group_cols,
        )

    source, plots = _create_plots(
        df=hist_data,
        value_col=value_col,
        group_cols=group_cols,
        subgroup_col=subgroup_col,
        id_col=id_col,
        lower_bound_col=lower_bound_col,
        upper_bound_col=upper_bound_col,
        width=width,
        axis_for_every_parameter=axis_for_every_parameter,
    )
    grid = gridplot(plots, toolbar_location="right", ncols=1)
    show(grid)
    return source, grid


# =====================================================================================
#                                    PLOT FUNCTIONS
# =====================================================================================


def _create_plots(
    df,
    value_col,
    group_cols,
    subgroup_col,
    id_col,
    lower_bound_col,
    upper_bound_col,
    width,
    axis_for_every_parameter,
):
    source = ColumnDataSource(df)
    gb = df.groupby(group_cols)

    widgets = _create_group_widgets(
        df=df,
        source=source,
        subgroup_col=subgroup_col,
        lower_bound_col=lower_bound_col,
        upper_bound_col=upper_bound_col,
    )
    plots = [wid for wid in widgets if wid is not None]

    old_group = np.nan
    for (group1, group2), group_df in gb:
        new_group = old_group != group1
        if new_group:
            plots.append(title_fig(group_cols[0], group1))
            x_range = _calculate_x_range(
                df=df,
                lower_bound_col=lower_bound_col,
                upper_bound_col=upper_bound_col,
                group_col=group_cols[-2],
                group_val=group1,
            )
            old_group = group1

        filters = _create_filters(
            source=source,
            group_df=group_df,
            subgroup_col=subgroup_col,
            id_col=id_col,
            widgets=widgets,
        )
        view = CDSView(source=source, filters=filters)

        param_plot = _create_base_plot(
            group1=group1,
            group2=group2,
            group_df=group_df,
            source=source,
            view=view,
            x_range=x_range,
            width=width,
            id_col=id_col,
        )

        param_plot = _add_ci_bars_if_present(
            param_plot=param_plot,
            source=source,
            view=view,
            lower_bound_col=lower_bound_col,
            upper_bound_col=upper_bound_col,
        )

        _style_plot(
            fig=param_plot,
            new_group=new_group,
            axis_for_every_parameter=axis_for_every_parameter,
        )
        plots.append(param_plot)

    plots = _add_value_slider_in_front(
        df=df,
        value_col=value_col,
        lower_bound_col=lower_bound_col,
        upper_bound_col=upper_bound_col,
        plots=plots,
    )
    return source, plots


def _create_group_widgets(df, source, lower_bound_col, upper_bound_col, subgroup_col):
    sr = df[subgroup_col]
    checkboxes = None
    slider = None
    if sr.dtype == float:
        sorted_uniques = np.array(sorted(sr.unique()[::-1]))
        min_dist_btw_vals = (sorted_uniques[1:] - sorted_uniques[:-1]).min()
        slider = RangeSlider(
            start=sr.min(),
            end=sr.max(),
            value=(sr.min(), sr.max()),
            step=min_dist_btw_vals,
            title=subgroup_col.title(),
        )
        slider.js_on_change(
            "value", CustomJS(code="source.change.emit();", args={"source": source})
        )
    elif sr.dtype == object:
        checkbox_labels = df[subgroup_col].unique().tolist()
        checkboxes = CheckboxGroup(
            labels=checkbox_labels, active=list(range(len(checkbox_labels)))
        )
        checkboxes.js_on_change(
            "active", CustomJS(code="source.change.emit();", args={"source": source})
        )

    return (
        checkboxes,
        slider,
    )


def _calculate_x_range(df, lower_bound_col, upper_bound_col, group_col, group_val):
    whole_group_df = df[df[group_col] == group_val]
    rect_width = whole_group_df["rect_width"].unique()[0]
    group_min = whole_group_df["binned_x"].min() - rect_width
    group_max = whole_group_df["binned_x"].max() + rect_width
    if lower_bound_col is not None:
        group_min = min(group_min, whole_group_df[lower_bound_col].min())
    if upper_bound_col is not None:
        group_max = max(group_max, whole_group_df[upper_bound_col].max())
    return group_min, group_max


def _create_base_plot(group1, group2, group_df, source, view, x_range, width, id_col):
    param_plot = figure(
        title=str(group2).title(),
        plot_height=200,
        plot_width=width,
        tools="reset,save",
        y_axis_location="left",
        x_range=x_range,
    )
    point_glyph = param_plot.rect(
        source=source,
        view=view,
        x="binned_x",
        width="rect_width",
        y="dodge",
        height=1,
        color="color",
        selection_color="color",
        nonselection_color="color",
        alpha=0.5,
        selection_alpha=0.7,
        nonselection_alpha=0.1,
    )

    param_plot = _add_hover_tool(param_plot, point_glyph, group_df)
    param_plot = _add_select_tools(param_plot, point_glyph, source, id_col)
    return param_plot


def _add_ci_bars_if_present(param_plot, source, view, lower_bound_col, upper_bound_col):
    if lower_bound_col is not None and upper_bound_col is not None:
        param_plot.hbar(
            source=source,
            view=view,
            y="dodge",
            left=lower_bound_col,
            right=upper_bound_col,
            height=0.01,
            alpha=0.0,
            selection_alpha=0.7,
            nonselection_alpha=0.0,
            color="color",
            selection_color="color",
            nonselection_color="color",
        )
    return param_plot


# =====================================================================================
#                                  CALLBACK FUNCTIONS
# =====================================================================================


def _create_filters(source, group_df, subgroup_col, id_col, widgets):
    filters = [IndexFilter(group_df.index)]
    checkboxes, group_slider = widgets
    if checkboxes is not None:
        checkbox_filter = _create_checkbox_filter(checkboxes, source, subgroup_col)
        filters.append(checkbox_filter)
    if group_slider is not None:
        group_slider_filter = _create_slider_filter(group_slider, source, subgroup_col)
        filters.append(group_slider_filter)
    return filters


def _create_checkbox_filter(checkboxes, source, subgroup_col):
    code = (
        """
    let selected = checkboxes.active.map(i=>checkboxes.labels[i]);
    let indices = [];
    let column = source.data."""
        + subgroup_col
        + """;
    for(let i=0; i<column.length; i++){
        if(selected.includes(column[i])){
            indices.push(i);
        }
    }
    return indices;
    """
    )

    checkbox_filter = CustomJSFilter(
        code=code, args={"checkboxes": checkboxes, "source": source}
    )

    return checkbox_filter


def _create_slider_filter(slider, source, column):
    code = (
        """
    let lower_bound = slider.value[0];
    let upper_bound = slider.value[1];
    let indices = [];
    let column = source.data."""
        + column
        + """;
    for(let i=0; i<column.length; i++){
        if(lower_bound <= column[i]){
            if (column[i] <= upper_bound){
                indices.push(i);
            }
        }
    }
    return indices;
    """
    )

    slider_filter = CustomJSFilter(code=code, args={"slider": slider, "source": source})

    return slider_filter


def _add_hover_tool(param_plot, point_glyph, df):
    skip = ["dodge", "color", "binned_x", "level_0"]
    to_display = [
        col for col in df.columns if len(df[col].unique()) > 1 and col not in skip
    ]
    tooltips = [(col, "@" + col) for col in to_display]
    hover = HoverTool(renderers=[point_glyph], tooltips=tooltips)
    param_plot.tools.append(hover)
    return param_plot


def _add_select_tools(param_plot, point_glyph, source, id_col):
    select_kwargs = {"source": source}
    select_code = (
        """
    // adapted from https://stackoverflow.com/a/44996422

    var chosen = source.selected.indices;
    if (typeof(chosen) == "number"){
        var chosen = [chosen]
    };

    var chosen_ids = [];
    for (var i = 0; i < chosen.length; ++ i){
        chosen_ids.push(source.data['"""
        + id_col
        + """'][chosen[i]])
    };

    var chosen_ids_indices = [];
    for (var i = 0; i < source.data['index'].length; ++ i){
        if (chosen_ids.includes(source.data['"""
        + id_col
        + """'][i])){
            chosen_ids_indices.push(i)
        };
    };
    source.selected.indices = chosen_ids_indices;
    source.change.emit();
    """
    )
    select_callback = CustomJS(args=select_kwargs, code=select_code)
    # point_glyph as only renderer assures that when a point is chosen
    # only that brick's id is chosen
    # this makes it impossible to choose ids based on clicking confidence bands
    tap = TapTool(renderers=[point_glyph], callback=select_callback)
    param_plot.tools.append(tap)
    boxselect = BoxSelectTool(renderers=[point_glyph], callback=select_callback)
    param_plot.tools.append(boxselect)
    return param_plot


def _add_value_slider_in_front(df, value_col, lower_bound_col, upper_bound_col, plots):
    val_min = df[value_col].min()
    val_max = df[value_col].max()
    if lower_bound_col is not None:
        val_min = min(val_min, df[lower_bound_col].min())
    if upper_bound_col is not None:
        val_max = max(val_max, df[upper_bound_col].max())
    x_range = val_max - val_min
    value_column_slider = RangeSlider(
        start=val_min - 0.02 * x_range,
        end=val_max + 0.02 * x_range,
        value=(val_min, val_max),
        step=x_range / 500,
        title=value_col.title(),
    )

    code = """
        var lower_end = cb_obj.value[0]
        var upper_end = cb_obj.value[1]

        for (var i = 0; i < plots.length; ++ i){
            plots[i].x_range.start = lower_end;
            plots[i].x_range.end = upper_end;
        }
    """

    callback = CustomJS(args={"plots": plots[1:]}, code=code)
    value_column_slider.js_on_change("value", callback)
    return [value_column_slider] + plots


# =====================================================================================
#                                    DATA FUNCTIONS
# =====================================================================================


def add_histogram_columns_to_tidy_df(
    df, group_cols, subgroup_col, value_col, id_col, num_bins, x_padding
):
    drop_if_nan_cols = group_cols + [subgroup_col, value_col, id_col]
    hist_data = df[df[drop_if_nan_cols].notnull().all(axis=1)]
    hist_data.sort_values(group_cols + [subgroup_col, value_col], inplace=True)
    hist_data.reset_index(inplace=True)
    hist_data[["binned_x", "rect_width"]] = _bin_width_and_midpoints(
        df=hist_data,
        group_cols=group_cols,
        value_col=value_col,
        num_bins=num_bins,
        x_padding=x_padding,
    )

    hist_data["dodge"] = 0.5 + hist_data.groupby(group_cols + ["binned_x"]).cumcount()
    hist_data[subgroup_col] = _clean_subgroup_col(sr=hist_data[subgroup_col])
    hist_data["color"] = _create_color_col(sr=hist_data[subgroup_col])
    return hist_data


def _bin_width_and_midpoints(df, group_cols, value_col, num_bins, x_padding):
    # Exclude the last column because the last column identifies the plot
    # but we want the bins to be comparable across plots of the same group.
    group_keys = group_cols[:-1]
    grouped = df.groupby(group_keys)
    bin_width_and_midpoints = partial(
        _bin_width_and_midpoints_per_group,
        value_col=value_col,
        num_bins=num_bins,
        x_padding=x_padding,
    )
    return grouped.apply(bin_width_and_midpoints)


def _bin_width_and_midpoints_per_group(df, value_col, num_bins, x_padding):
    xmin, xmax = _calculate_x_bounds(df, value_col, x_padding)
    bins, rect_width = np.linspace(
        start=xmin, stop=xmax, num=num_bins + 1, retstep=True
    )
    midpoints = bins[:-1] + rect_width / 2
    values_midpoints = pd.cut(df[value_col], bins, labels=midpoints).astype(float)
    to_add = values_midpoints.to_frame(name="binned_x")
    to_add["rect_width"] = rect_width
    return to_add


def _calculate_x_bounds(df, value_col, padding):
    raw_min = df[value_col].min()
    raw_max = df[value_col].max()
    white_space = (raw_max - raw_min).clip(1e-50) * padding
    x_min = raw_min - white_space
    x_max = raw_max + white_space
    return x_min, x_max


def _clean_subgroup_col(sr):
    if len(sr.unique()) < 10:
        sr = sr.astype(str)
    else:
        try:
            sr = sr.astype(float)
        except ValueError:
            sr = sr.astype(str)
    return sr


def _create_color_col(sr):
    subgroup_vals = sr.unique()
    palette = get_color_palette(len(subgroup_vals))
    color_dict = {val: color for val, color in zip(subgroup_vals, palette)}
    return sr.replace(color_dict)


# =====================================================================================
#                                   STYLING FUNCTIONS
# =====================================================================================


def _determine_plot_height(figure_height, data, group_cols):
    """Calculate the height alloted to each plot in pixels.

    Args:
        figure_height (int): height of the entire figure in pixels
        data (pd.DataFrame): DataFrame of the

    Returns:
        plot_height (int): Plot height in pixels.

    """
    if figure_height is None:
        figure_height = int(max(min(30 * data["dodge"].max(), 1000), 100))

    n_groups = len(data.groupby(group_cols[:-1]))
    n_params = len(data.groupby(group_cols))
    space_of_titles = n_groups * 50
    available_space = figure_height - space_of_titles
    plot_height = int(available_space / n_params)
    if plot_height < 50:
        warnings.warn(
            "The figure height you specified results in very small "
            "plots which may not render well. Adjust the figure height "
            "to a larger value or set it to None to get a larger plot. "
            "Alternatively, you can click on the Reset button "
            "on the right of the plot and your plot should render correctly."
        )
    return plot_height


def _style_plot(fig, new_group, axis_for_every_parameter):
    _style_x_axis(
        fig=fig, new_group=new_group, axis_for_every_parameter=axis_for_every_parameter
    )
    _style_y_axis(fig=fig)

    fig.title.vertical_align = "top"
    fig.title.text_alpha = 70
    fig.title.text_font_style = "normal"
    fig.outline_line_color = None
    fig.min_border_top = 20
    fig.min_border_bottom = 20
    fig.xgrid.visible = False
    fig.ygrid.visible = False
    fig.sizing_mode = "scale_width"


def _style_x_axis(fig, new_group, axis_for_every_parameter):
    if not axis_for_every_parameter:
        if new_group:
            fig.xaxis.visible = False
        else:
            fig.xaxis.axis_line_color = None
        xmin = fig.x_range.start
        xmax = fig.x_range.end
        fig.line([xmin, xmax], [0, 0], line_color="black")
    fig.xaxis.minor_tick_line_color = None


def _style_y_axis(fig):
    fig.yaxis.minor_tick_line_color = None
    fig.yaxis.axis_line_color = None
    fig.yaxis.major_tick_line_color = None


def title_fig(group_type, group_name, width=500, level=0):
    title = Title(
        text="Distribution in {group_type} {group_name}".format(
            group_type=str(group_type).title(), group_name=str(group_name).title()
        ),
        align="center",
        text_font_size="{}pt".format(15 - 2 * level),
    )
    fig = figure(title=title, plot_height=50, plot_width=width, tools="reset,save",)
    fig.line([], [])  # add renderer to avoid warning
    fig.ygrid.visible = False
    fig.xgrid.visible = False
    fig.outline_line_color = None
    fig.yaxis.axis_line_color = None
    fig.xaxis.axis_line_color = None

    return fig

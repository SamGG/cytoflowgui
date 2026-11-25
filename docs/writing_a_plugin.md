# Writing a plugin

A plugin is a folder with four files: a small Qt form (`gui.py`) that collects parameters, and an
RMarkdown script (`script.Rmd`) that the application renders with R. The application owns everything
else: the step label and comment, the Help and Run buttons, the step folder, the R process, the log.

## Quick start

1. Choose **? > Plugins > Create a plugin…** and answer: name, id, category, description, and the kind of
   input (none, one FCS file, a folder of FCS files, an RDS file, a table).
2. The plugin is written to the plugins folder and appears in the **Tools** menu. It already runs: it
   has an example text and an example option.
3. Edit its files, then use **? > Plugins > Reload plugins**. Reload the tool from the Tools menu to see form changes.

The plugins folder is shown in Preferences. When running from source, `CYTOFLOW_PLUGINS_DIR` sets it.

## The four files

### plugin.toml

Required: `id, name, category, tooltip, version, api_version, entry, rmd`. `api_version` must equal the
API of the application (the template fills it in). Optional: `help`, `[requires]`, `[[inputs]]`.

- `[requires] app` is the minimum application version. `r` lists the CRAN packages the script uses,
  `bioc` the Bioconductor ones. List every package: the application checks them before the first run.
- Each file or folder the user picks is declared in `[[inputs]]` (`param`, `kind` = fcs | rds | table |
  any, `folder`, `lineage`, `label`). With `lineage = true` the field offers **From step…** and the
  application records which step produced the file.
- `user = true` marks a plugin created in the application: updates never replace it.

### gui.py

A class derived from `ToolPlugin` (import it from `cytoflowgui.api`):

| Method | Role |
|---|---|
| `build_ui()` | add rows to `self.form`, set `self.intro` |
| `validate()` | return an error message, or `None` |
| `collect_parameters()` | the parameters as a JSON-serialisable dict |
| `apply_parameters(p)` | the inverse: fills the form, returns warnings; this enables **Re-run** |

File fields come from `self.input_picker("<param>")`, for a `param` declared in `[[inputs]]`.
`self.setting(key)` and `self.set_setting(key, value)` remember a value per plugin. Reusable widgets are in
`cytoflowgui.widgets`.

### script.Rmd

The YAML `params:` has exactly `params_file: ""`. The first chunk reads the parameters and loads the shared
helpers:

```r
cfg <- jsonlite::read_json(params$params_file, simplifyVector = TRUE)
source(file.path(cfg$helpers_dir, "helpers.R"), local = TRUE)
```

`cfg` holds your parameters plus the step context: `step_id`, `label`, `comment`, `step_dir`, `data_dir`,
`reports_dir`, `workspace_dir`, `helpers_dir`, `input_steps`, `plugin_id`, `plugin_version`.

- Write data that later steps continue from (FCS, RDS) to `cfg$data_dir`, and end products (XLSX, text) to
  `cfg$reports_dir`. Create the folder first. Build paths with `file.path()`; never hardcode them.
- Shared helpers: `log_msg()` (a progress line in the application log), `with_log()` (sends messages and
  warnings of a call to the log), `note()` / `print_notes()`, `is_text()`, `is_flag()`, `as_chr()`,
  `safe_name()`, `list_fcs_files()`, `write_session_info()`, `step_info()` (summary table) and
  `report_only_from_here()` (call it once the results are saved: later chunk errors stay in the report
  instead of failing the step).
- End the script with the shared footer, a chunk with
  `child = file.path(cfg$helpers_dir, "footer.Rmd")`.

### help.html

The text of the Help button: what each field means, and what the step writes.

## Running and debugging

Each run creates a step folder in the workspace with `run_parameters.json`, `console.log` (everything the
script wrote), `<id>_report.html` and `session_info.txt`.

To debug step by step in RStudio, open the plugin's `script.Rmd`, set the working folder to the step folder,
and run in the console `params <- list(params_file = "run_parameters.json")`. Then run the chunks in order.

## Publishing

A plugin you want to share goes to the plugins repository (`plugins/<id>/`). Before that, remove
`user = true`, set the version, and make sure `[requires]` is complete. `tools/make_index.py` packs the
plugins and refuses a plugin that still has `user = true`.

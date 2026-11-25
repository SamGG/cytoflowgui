# helpers.R -- R helpers shared by the plugin scripts (script.Rmd), shipped with
# cytoflowgui. The folder holding this file is given to the script as
# `cfg$helpers_dir` in run_parameters.json.
#
# Load it right after reading the configuration, in the same chunk:
#
#   cfg <- jsonlite::read_json(params$params_file, simplifyVector = TRUE)
#   source(file.path(cfg$helpers_dir, "helpers.R"), local = TRUE)
#
# `local = TRUE` defines the functions in the environment of the report.
# Package functions are called with `::`, so nothing has to be attached.

# internal state (notes, saved knitr error hook)
.cf <- new.env(parent = emptyenv())
.cf$notes <- character(0)
.cf$default_error_hook <- NULL

# ---- console ---------------------------------------------------------------

# progress line, streamed into the GUI log (stderr)
log_msg <- function(...) {
  cat(sprintf("[INFO] %s\n", sprintf(...)), file = stderr())
}

# R messages and warnings of the evaluated expression go to the GUI log
with_log <- function(expr) {
  withCallingHandlers(
    expr,
    message = function(m) {
      cat(conditionMessage(m), file = stderr())
      invokeRestart("muffleMessage")
    },
    warning = function(w) {
      cat("WARNING: ", conditionMessage(w), "\n", sep = "", file = stderr())
      invokeRestart("muffleWarning")
    }
  )
}

# ---- notes: collected, logged, and printed in the report -----------------

note <- function(...) {
  m <- sprintf(...)
  .cf$notes <- c(.cf$notes, m)
  log_msg("NOTE: %s", m)
  invisible(m)
}

# call in a chunk with results = 'asis'; prints nothing when there is no note
print_notes <- function() {
  if (length(.cf$notes)) {
    cat("\n**Notes**\n\n")
    cat(paste0("- ", .cf$notes, "\n"), sep = "")
  }
  invisible(NULL)
}

# ---- small helpers -----------------------------------------------------------

# a single non-empty string
is_text <- function(x) {
  is.character(x) && length(x) == 1L && nchar(trimws(x)) > 0
}

# a single TRUE or FALSE
is_flag <- function(x) {
  is.logical(x) && length(x) == 1L && !is.na(x)
}

# any JSON value (NULL, scalar, list) as a trimmed character vector
as_chr <- function(x) {
  if (is.null(x) || length(x) == 0) {
    return(character(0))
  }
  trimws(as.character(unlist(x)))
}

# a text usable inside a file name
safe_name <- function(x) {
  trimws(gsub("[^[:alnum:]._ -]+", "_", x))
}

# ---- FCS -----------------------------------------------------------

# the .fcs files of a folder (not recursive); stops when there is none
list_fcs_files <- function(dir) {
  log_msg("Scanning for FCS files in: %s", dir)
  dir <- normalizePath(dir, mustWork = TRUE)
  files <- list.files(
    path = dir, pattern = "\\.fcs$", full.names = TRUE, ignore.case = TRUE
  )
  if (length(files) == 0L) {
    stop("No FCS files found in: ", dir)
  }
  log_msg("%d FCS file(s) found", length(files))
  for (i in seq_along(files)) {
    log_msg("  %d. %s", i, basename(files[i]))
  }
  files
}

# ---- report --------------------------------------------------------

# Call it once the results are saved: from the next chunk on, an error in a
# chunk no longer fails the step. It stays in the report, is written to the
# GUI log, and the following chunks run. Earlier chunks still stop the render.
report_only_from_here <- function() {
  knitr::opts_chunk$set(error = TRUE)
  if (is.null(.cf$default_error_hook)) {
    .cf$default_error_hook <- knitr::knit_hooks$get("error")
  }
  knitr::knit_hooks$set(
    error = function(x, options) {
      log_msg("WARNING: report chunk '%s' failed: %s", options$label, trimws(x))
      .cf$default_error_hook(x, options)
    }
  )
  invisible(NULL)
}

# session_info.txt in the step folder
write_session_info <- function(cfg) {
  writeLines(
    capture.output(sessionInfo()),
    file.path(cfg$step_dir, "session_info.txt")
  )
}

# Summary table: the step, the step that produced the input, then the items
# of the plugin, a named vector of single values: c("Cytometer" = "flow", ...).
# `input_param` is the name of the input parameter of plugin.toml, or NULL
# for a plugin without input.
step_info <- function(cfg, input_param, items = character(0)) {
  if (is.null(input_param)) {
    input_step <- "none"
  } else {
    input_step <- cfg$input_steps[[input_param]]
    if (is.null(input_step) || !nzchar(input_step)) {
      input_step <- "none (outside the workspace)"
    }
  }
  data.frame(
    Item = c("Step", "Input step", names(items)),
    Value = c(paste(cfg$step_id, cfg$label), input_step, as.character(items)),
    stringsAsFactors = FALSE
  )
}

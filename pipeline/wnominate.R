# Scale each legislature-chamber vote matrix written by validate_wnominate.py with W-NOMINATE (2 dimensions).
# Usage: Rscript pipeline/wnominate.R data/processed/wnominate [k n]
# With k and n it does only every n-th job, starting at the k-th (0-based), so n copies can share the work.
suppressMessages({library(pscl); library(wnominate)})
args <- commandArgs(trailingOnly = TRUE)
dir <- args[1]
jobs <- read.table(file.path(dir, "jobs.txt"), colClasses = "character")
for (i in seq_len(nrow(jobs))) {
  if (length(args) >= 3 && (i - 1) %% as.integer(args[3]) != as.integer(args[2])) next
  name <- jobs[i, 1]
  out <- file.path(dir, paste0(name, ".coords.csv"))
  if (file.exists(out)) next
  m <- read.csv(file.path(dir, paste0(name, ".csv")), check.names = FALSE, colClasses = "character")
  ids <- m[[1]]
  v <- as.matrix(m[, -1]); storage.mode(v) <- "numeric"; rownames(v) <- ids
  rc <- rollcall(v, yea = 1, nay = 6, missing = 9, notInLegis = 0, legis.names = ids)
  p1 <- match(jobs[i, 2], ids); p2 <- match(jobs[i, 3], ids)
  fit <- tryCatch(wnominate(rc, dims = 2, polarity = c(p1, p2), verbose = FALSE),
                  error = function(e) { message(name, ": ", conditionMessage(e)); NULL })
  if (is.null(fit)) next
  write.csv(data.frame(member_id = ids, coord1D = fit$legislators$coord1D, coord2D = fit$legislators$coord2D),
            out, row.names = FALSE)
  message(name, " done")
}

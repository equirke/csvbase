# csvbase

## Getting CSV test data

The CSV format is hairy.

There is some good test data on https://people.sc.fsu.edu/~jburkardt/data/csv/csv.html

List of issues in the pg parser:
- trailing commas on the ends of lines not supported
- extra blank lines at the end of the file not supported
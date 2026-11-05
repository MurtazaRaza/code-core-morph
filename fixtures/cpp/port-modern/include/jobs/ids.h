#pragma once

namespace jobs {

// The ids of new jobs: 1, 2, 3, ... across the process, from any thread.
int next_id();

// Starts the ids at 1 again.
void reset_ids();

} // namespace jobs

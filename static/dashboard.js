/**
 * Performance Dashboard JavaScript
 * Handles data fetching, checkbox filtering, matrix rendering, and task detail expansion
 */

// Global data storage
let dashboardData = null;

// Cookie management functions
function setCookie(name, value, days = 30) {
    const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toUTCString();
    document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/`;
}

function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) {
        return decodeURIComponent(parts.pop().split(';').shift());
    }
    return null;
}

// Load preferences from cookies
function loadPreferences() {
    const peopleStr = getCookie('tracked_people');
    const spacesStr = getCookie('tracked_spaces');
    
    return {
        people: peopleStr ? JSON.parse(peopleStr) : [],
        spaces: spacesStr ? JSON.parse(spacesStr) : []
    };
}

// Save preferences to cookies
function savePreferences(people, spaces) {
    setCookie('tracked_people', JSON.stringify(people));
    setCookie('tracked_spaces', JSON.stringify(spaces));
}

// Get currently selected people and spaces from checkboxes
function getSelectedFilters() {
    const selectedPeople = [];
    const selectedSpaces = [];
    
    document.querySelectorAll('#people-checkboxes input[type="checkbox"]:checked').forEach(cb => {
        selectedPeople.push(cb.value);
    });
    
    document.querySelectorAll('#space-checkboxes input[type="checkbox"]:checked').forEach(cb => {
        selectedSpaces.push(cb.value);
    });
    
    return { people: selectedPeople, spaces: selectedSpaces };
}

// Update filter labels to show counts
function updateFilterLabels() {
    const filters = getSelectedFilters();
    const totalPeople = document.querySelectorAll('#people-checkboxes input[type="checkbox"]').length;
    const totalSpaces = document.querySelectorAll('#space-checkboxes input[type="checkbox"]').length;
    
    document.getElementById('people-label').textContent = 
        `People (${filters.people.length} of ${totalPeople} selected)`;
    document.getElementById('spaces-label').textContent = 
        `Spaces (${filters.spaces.length} of ${totalSpaces} selected)`;
}

// Toggle filter panel visibility
function toggleFilterPanel(panelId, toggleId) {
    const panel = document.getElementById(panelId);
    const toggle = document.getElementById(toggleId);
    const arrow = toggle.querySelector('.filter-arrow');
    
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        arrow.textContent = '▲';
        toggle.classList.add('active');
    } else {
        panel.style.display = 'none';
        arrow.textContent = '▼';
        toggle.classList.remove('active');
    }
}

// Select or deselect all checkboxes in a container
function setAllCheckboxes(containerId, checked) {
    document.querySelectorAll(`#${containerId} input[type="checkbox"]`).forEach(cb => {
        cb.checked = checked;
    });
    
    const filters = getSelectedFilters();
    savePreferences(filters.people, filters.spaces);
    updateFilterLabels();
    renderMatrix(filters.people, filters.spaces);
}

// Calculate metrics from raw tasks
function calculateMetrics(tasks, selectedPeople, selectedSpaces) {
    const metrics = {};
    
    // Initialize metrics structure
    selectedPeople.forEach(person => {
        metrics[person] = {};
        selectedSpaces.forEach(space => {
            metrics[person][space] = {
                assigned: 0,
                completed: 0
            };
        });
        // Add total column
        metrics[person]['_total'] = {
            assigned: 0,
            completed: 0
        };
    });
    
    // Calculate metrics from tasks
    tasks.forEach(task => {
        const assignee = task.assignee;
        const space = task.space_name;
        const status = task.status;
        
        // Count tasks assigned
        if (selectedPeople.includes(assignee) && selectedSpaces.includes(space)) {
            metrics[assignee][space].assigned++;
            metrics[assignee]['_total'].assigned++;
            
            // Count tasks completed
            if (status === 'COMPLETED') {
                metrics[assignee][space].completed++;
                metrics[assignee]['_total'].completed++;
            }
        }
    });
    
    return metrics;
}

// Render checkboxes for people and spaces
function renderCheckboxes() {
    if (!dashboardData) return;
    
    const prefs = loadPreferences();
    const peopleContainer = document.getElementById('people-checkboxes');
    const spacesContainer = document.getElementById('space-checkboxes');
    
    peopleContainer.innerHTML = '';
    spacesContainer.innerHTML = '';
    
    // Always use all people (space filtering is now done server-side via config)
    const peopleList = dashboardData.all_people;
    
    // Render people checkboxes
    peopleList.forEach(person => {
        const label = document.createElement('label');
        label.className = 'checkbox-label';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = person;
        checkbox.checked = prefs.people.length === 0 || prefs.people.includes(person);
        
        checkbox.addEventListener('change', () => {
            const filters = getSelectedFilters();
            savePreferences(filters.people, filters.spaces);
            updateFilterLabels();
            renderMatrix(filters.people, filters.spaces);
        });
        
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(' ' + person));
        peopleContainer.appendChild(label);
    });
    
    // Render space checkboxes
    dashboardData.spaces.forEach(space => {
        const label = document.createElement('label');
        label.className = 'checkbox-label';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = space.name;
        checkbox.checked = prefs.spaces.length === 0 || prefs.spaces.includes(space.name);
        
        checkbox.addEventListener('change', () => {
            const filters = getSelectedFilters();
            savePreferences(filters.people, filters.spaces);
            updateFilterLabels();
            renderMatrix(filters.people, filters.spaces);
        });
        
        const displayName = space.displayName || space.name;
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(' ' + displayName));
        spacesContainer.appendChild(label);
    });
    
    // Update filter labels after rendering checkboxes
    updateFilterLabels();
}

// Render performance matrix table
function renderMatrix(selectedPeople, selectedSpaces) {
    if (!dashboardData || selectedPeople.length === 0 || selectedSpaces.length === 0) {
        document.getElementById('performance-matrix').innerHTML = '<tr><td>Please select at least one person and one space</td></tr>';
        return;
    }
    
    const metrics = calculateMetrics(dashboardData.tasks, selectedPeople, selectedSpaces);
    const table = document.getElementById('performance-matrix');
    table.innerHTML = '';
    
    // Create header row - NOW WITH PEOPLE AS COLUMNS
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    
    // Add person columns
    selectedPeople.forEach(person => {
        headerRow.appendChild(createHeaderCell(person, false)); // Rotated column
    });
    
    // Add total column
    headerRow.appendChild(createHeaderCell('Total', false)); // Rotated column
    
    // Add space column at the end
    headerRow.appendChild(createHeaderCell('Space', true)); // Mark as last column, not rotated
    thead.appendChild(headerRow);
    table.appendChild(thead);
    
    // Create body rows - NOW SPACES ARE ROWS
    const tbody = document.createElement('tbody');
    
    // Create TOTAL ROW FIRST (at the top)
    const totalRow = document.createElement('tr');
    totalRow.className = 'total-row';
    
    // Calculate column totals (total per person across all spaces)
    selectedPeople.forEach(person => {
        const personTotal = metrics[person]['_total'];
        const cell = createMetricCell(person, '_total', personTotal);
        cell.className = 'metric-cell total-cell';
        totalRow.appendChild(cell);
    });
    
    // Grand total
    let grandAssigned = 0, grandCompleted = 0;
    selectedPeople.forEach(person => {
        grandAssigned += metrics[person]['_total'].assigned;
        grandCompleted += metrics[person]['_total'].completed;
    });
    const grandCell = createMetricCell('_all', '_total', {
        assigned: grandAssigned,
        completed: grandCompleted
    });
    grandCell.className = 'metric-cell total-cell grand-total';
    totalRow.appendChild(grandCell);
    
    // Add "Total" label in the last column (space column)
    const totalLabelCell = document.createElement('td');
    totalLabelCell.textContent = 'Total';
    totalLabelCell.className = 'space-cell total-cell';
    totalLabelCell.style.fontWeight = '700';
    totalRow.appendChild(totalLabelCell);
    
    tbody.appendChild(totalRow);
    
    // Now add individual space rows
    selectedSpaces.forEach(spaceId => {
        const space = dashboardData.spaces.find(s => s.name === spaceId);
        const displayName = space ? (space.displayName || spaceId) : spaceId;
        const row = document.createElement('tr');
        
        // Person metric cells
        selectedPeople.forEach(person => {
            const metric = metrics[person][spaceId];
            const cell = createMetricCell(person, spaceId, metric);
            row.appendChild(cell);
        });
        
        // Row total cell (total for this space across all people)
        let rowAssigned = 0, rowCompleted = 0;
        selectedPeople.forEach(person => {
            rowAssigned += metrics[person][spaceId].assigned;
            rowCompleted += metrics[person][spaceId].completed;
        });
        const totalCell = createMetricCell('_all', spaceId, {
            assigned: rowAssigned,
            completed: rowCompleted
        });
        totalCell.className = 'metric-cell total-cell';
        row.appendChild(totalCell);
        
        // Space name cell at the end
        const nameCell = document.createElement('td');
        nameCell.className = 'space-cell';
        nameCell.textContent = displayName;
        nameCell.title = displayName; // Show full name on hover
        row.appendChild(nameCell);
        
        tbody.appendChild(row);
    });
    table.appendChild(tbody);
}

// Helper function to create header cell
function createHeaderCell(text, isFirstColumn = false) {
    const th = document.createElement('th');
    
    if (isFirstColumn) {
        // First column (Person) - no rotation
        th.textContent = text;
    } else {
        // Space columns - add rotated div
        const rotatedDiv = document.createElement('div');
        rotatedDiv.className = 'rotated-header';
        rotatedDiv.textContent = text;
        rotatedDiv.title = text; // Full name on hover
        th.appendChild(rotatedDiv);
    }
    
    return th;
}

// Helper function to create metric cell with clickable numbers
function createMetricCell(person, space, metric) {
    const cell = document.createElement('td');
    cell.className = 'metric-cell';
    
    // Create two clickable spans for each metric
    const assignedSpan = document.createElement('span');
    assignedSpan.className = 'metric-number assigned';
    assignedSpan.textContent = metric.assigned;
    assignedSpan.title = 'Tasks Assigned';
    if (metric.assigned > 0) {
        assignedSpan.style.cursor = 'pointer';
        assignedSpan.addEventListener('click', () => showTaskDetails(person, space, 'assigned'));
    }
    
    const completedSpan = document.createElement('span');
    completedSpan.className = 'metric-number completed';
    completedSpan.textContent = metric.completed;
    completedSpan.title = 'Tasks Completed';
    if (metric.completed > 0) {
        completedSpan.style.cursor = 'pointer';
        completedSpan.addEventListener('click', () => showTaskDetails(person, space, 'completed'));
    }
    
    cell.appendChild(assignedSpan);
    cell.appendChild(document.createTextNode(' / '));
    cell.appendChild(completedSpan);
    
    return cell;
}

// Show task details when clicking a number
function showTaskDetails(person, space, metric) {
    if (!dashboardData || person === '_all') return;
    
    // Filter tasks based on criteria
    let filteredTasks = [];
    
    if (metric === 'assigned') {
        filteredTasks = dashboardData.tasks.filter(task => 
            task.assignee === person && 
            (space === '_total' || task.space_name === space)
        );
    } else if (metric === 'completed') {
        filteredTasks = dashboardData.tasks.filter(task => 
            task.assignee === person && 
            task.status === 'COMPLETED' &&
            (space === '_total' || task.space_name === space)
        );
    }
    
    // Update details section
    const detailsSection = document.getElementById('task-details');
    const detailsTitle = document.getElementById('details-title');
    const detailsTable = document.getElementById('details-table');
    
    // Set title
    const metricText = metric === 'assigned' ? 'Assigned' : 'Completed';
    const spaceText = space === '_total' ? 'All Spaces' : (dashboardData.spaces.find(s => s.name === space)?.displayName || space);
    detailsTitle.textContent = `${metricText} Tasks - ${person} - ${spaceText} (${filteredTasks.length})`;
    
    // Build details table
    detailsTable.innerHTML = '';
    
    if (filteredTasks.length === 0) {
        detailsTable.innerHTML = '<tr><td>No tasks found</td></tr>';
    } else {
        // Create header
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        ['Task ID', 'Created', 'Assignee', 'Status', 'Space', 'First Message'].forEach(text => {
            const th = document.createElement('th');
            th.textContent = text;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        detailsTable.appendChild(thead);
        
        // Create body
        const tbody = document.createElement('tbody');
        filteredTasks.forEach(task => {
            const row = document.createElement('tr');
            
            // Task ID
            const idCell = document.createElement('td');
            idCell.textContent = task.id || 'N/A';
            row.appendChild(idCell);
            
            // Created time
            const createdCell = document.createElement('td');
            const createdDate = new Date(task.created_time);
            createdCell.textContent = createdDate.toLocaleString();
            row.appendChild(createdCell);
            
            // Assignee
            const assigneeCell = document.createElement('td');
            assigneeCell.textContent = task.assignee || 'N/A';
            row.appendChild(assigneeCell);
            
            // Status
            const statusCell = document.createElement('td');
            statusCell.textContent = task.status || 'N/A';
            statusCell.className = task.status === 'COMPLETED' ? 'status-completed' : 'status-open';
            row.appendChild(statusCell);
            
            // Space
            const spaceCell = document.createElement('td');
            const taskSpace = dashboardData.spaces.find(s => s.name === task.space_name);
            spaceCell.textContent = taskSpace ? (taskSpace.displayName || task.space_name) : task.space_name;
            row.appendChild(spaceCell);
            
            // First thread message
            const messageCell = document.createElement('td');
            messageCell.className = 'message-cell';
            messageCell.textContent = task.first_thread_message || '(No message)';
            row.appendChild(messageCell);
            
            tbody.appendChild(row);
        });
        detailsTable.appendChild(tbody);
    }
    
    // Show details section
    detailsSection.style.display = 'block';
    detailsSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Aggregate data from multiple API responses (for progressive loading)
function aggregateData(dataArray) {
    if (dataArray.length === 0) return null;
    if (dataArray.length === 1) return dataArray[0];
    
    const aggregated = {
        date_start: dataArray[0].date_start,
        date_end: dataArray[dataArray.length - 1].date_end,
        tasks: [],
        spaces: [],
        all_people: new Set()
    };
    
    // Collect all tasks
    dataArray.forEach(data => {
        aggregated.tasks.push(...data.tasks);
    });
    
    // Collect unique spaces (by space name)
    const spaceMap = new Map();
    dataArray.forEach(data => {
        data.spaces.forEach(space => {
            if (!spaceMap.has(space.name)) {
                spaceMap.set(space.name, space);
            }
        });
    });
    aggregated.spaces = Array.from(spaceMap.values());
    
    // Collect unique people
    dataArray.forEach(data => {
        data.all_people.forEach(person => {
            aggregated.all_people.add(person);
        });
    });
    aggregated.all_people = Array.from(aggregated.all_people).sort();
    
    return aggregated;
}

// Update progress bar
function updateProgress(current, total, message) {
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    
    const percentage = Math.round((current / total) * 100);
    progressBar.style.width = percentage + '%';
    progressBar.textContent = percentage + '%';
    progressText.textContent = message;
    progressContainer.style.display = 'block';
}

// Fetch data from API (with progressive loading for 4-weeks)
async function fetchData() {
    const fetchBtn = document.getElementById('fetch-data-btn');
    const loadingMsg = document.getElementById('loading-message');
    const progressContainer = document.getElementById('progress-container');
    const errorMsg = document.getElementById('error-message');
    const mainContent = document.getElementById('main-content');
    
    // Show loading state
    fetchBtn.disabled = true;
    loadingMsg.style.display = 'block';
    progressContainer.style.display = 'none';
    errorMsg.style.display = 'none';
    
    try {
        const today = new Date();
        const scriptPath = window.location.pathname.split('?')[0];
        
        // Determine if we need progressive loading
        if (currentPeriod === '4-weeks') {
            // Progressive loading: fetch one week at a time
            loadingMsg.style.display = 'none';
            progressContainer.style.display = 'block';
            
            const weeklyData = [];
            const totalWeeks = 4;
            
            for (let week = 0; week < totalWeeks; week++) {
                updateProgress(week, totalWeeks, `Fetching week ${week + 1} of ${totalWeeks}...`);
                
                // Calculate date range for this week
                const weekEnd = new Date(today);
                weekEnd.setDate(weekEnd.getDate() - (week * 7));
                
                const weekStart = new Date(weekEnd);
                weekStart.setDate(weekStart.getDate() - 7);
                
                // Fetch data for this week
                const apiUrl = `${scriptPath}/api/fetch-data?start=${encodeURIComponent(weekStart.toISOString())}&end=${encodeURIComponent(weekEnd.toISOString())}`;
                const response = await fetch(apiUrl);
                
                if (!response.ok) {
                    throw new Error(`Failed to fetch week ${week + 1}: ${response.statusText} (${response.status})`);
                }
                
                const weekData = await response.json();
                weeklyData.push(weekData);
                
                // Update progress after successful fetch
                updateProgress(week + 1, totalWeeks, `Week ${week + 1} of ${totalWeeks} completed`);
            }
            
            // Aggregate all weekly data
            dashboardData = aggregateData(weeklyData);
            
        } else {
            // Single fetch for last-day or last-week
            let daysBack;
            if (currentPeriod === 'last-day') {
                daysBack = 1;
            } else if (currentPeriod === 'last-week') {
                daysBack = 7;
            } else {
                daysBack = 7; // Default to last week
            }
            
            const startDate = new Date(today);
            startDate.setDate(startDate.getDate() - daysBack);
            
            const apiUrl = `${scriptPath}/api/fetch-data?start=${encodeURIComponent(startDate.toISOString())}&end=${encodeURIComponent(today.toISOString())}`;
            const response = await fetch(apiUrl);
            
            if (!response.ok) {
                throw new Error(`Failed to fetch data: ${response.statusText} (${response.status})\nURL: ${apiUrl}`);
            }
            
            dashboardData = await response.json();
        }
        
        // Hide loading, show main content
        loadingMsg.style.display = 'none';
        progressContainer.style.display = 'none';
        document.getElementById('fetch-section').style.display = 'none';
        mainContent.style.display = 'block';
        
        // Initialize UI
        const prefs = loadPreferences();
        renderCheckboxes();
        
        // If no preferences, select all
        const filters = getSelectedFilters();
        if (filters.people.length === 0 || filters.spaces.length === 0) {
            // Check all boxes by default
            document.querySelectorAll('#people-checkboxes input[type="checkbox"]').forEach(cb => cb.checked = true);
            document.querySelectorAll('#space-checkboxes input[type="checkbox"]').forEach(cb => cb.checked = true);
            const newFilters = getSelectedFilters();
            savePreferences(newFilters.people, newFilters.spaces);
            renderMatrix(newFilters.people, newFilters.spaces);
        } else {
            renderMatrix(filters.people, filters.spaces);
        }
        
    } catch (error) {
        console.error('Error fetching data:', error);
        errorMsg.textContent = `Error: ${error.message}`;
        errorMsg.style.display = 'block';
        loadingMsg.style.display = 'none';
        progressContainer.style.display = 'none';
        fetchBtn.disabled = false;
    }
}

// Initialize page
document.addEventListener('DOMContentLoaded', () => {
    // Set up fetch button
    document.getElementById('fetch-data-btn').addEventListener('click', fetchData);
    
    // Set up close details button
    document.getElementById('close-details').addEventListener('click', () => {
        document.getElementById('task-details').style.display = 'none';
    });
    
    // Set up filter toggle buttons
    document.getElementById('people-toggle').addEventListener('click', () => {
        toggleFilterPanel('people-panel', 'people-toggle');
    });
    
    document.getElementById('spaces-toggle').addEventListener('click', () => {
        toggleFilterPanel('spaces-panel', 'spaces-toggle');
    });
    
    // Set up select all/deselect all buttons
    document.getElementById('select-all-people').addEventListener('click', () => {
        setAllCheckboxes('people-checkboxes', true);
    });
    
    document.getElementById('deselect-all-people').addEventListener('click', () => {
        setAllCheckboxes('people-checkboxes', false);
    });
    
    document.getElementById('select-all-spaces').addEventListener('click', () => {
        setAllCheckboxes('space-checkboxes', true);
    });
    
    document.getElementById('deselect-all-spaces').addEventListener('click', () => {
        setAllCheckboxes('space-checkboxes', false);
    });
});




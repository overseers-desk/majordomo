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

// Calculate metrics from raw tasks
function calculateMetrics(tasks, selectedPeople, selectedSpaces) {
    const metrics = {};
    
    // Initialize metrics structure
    selectedPeople.forEach(person => {
        metrics[person] = {};
        selectedSpaces.forEach(space => {
            metrics[person][space] = {
                assigned: 0,
                completed: 0,
                given: 0
            };
        });
        // Add total column
        metrics[person]['_total'] = {
            assigned: 0,
            completed: 0,
            given: 0
        };
    });
    
    // Calculate metrics from tasks
    tasks.forEach(task => {
        const assignee = task.assignee;
        const sender = task.sender;
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
        
        // Count tasks given (created/assigned by this person)
        if (selectedPeople.includes(sender) && selectedSpaces.includes(space)) {
            metrics[sender][space].given++;
            metrics[sender]['_total'].given++;
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
            renderMatrix(filters.people, filters.spaces);
        });
        
        const displayName = space.displayName || space.name;
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(' ' + displayName));
        spacesContainer.appendChild(label);
    });
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
    
    // Create header row
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    headerRow.appendChild(createHeaderCell('Person'));
    
    // Add space columns
    selectedSpaces.forEach(spaceId => {
        const space = dashboardData.spaces.find(s => s.name === spaceId);
        const displayName = space ? (space.displayName || spaceId) : spaceId;
        headerRow.appendChild(createHeaderCell(displayName));
    });
    
    // Add total column
    headerRow.appendChild(createHeaderCell('Total'));
    thead.appendChild(headerRow);
    table.appendChild(thead);
    
    // Create body rows
    const tbody = document.createElement('tbody');
    selectedPeople.forEach(person => {
        const row = document.createElement('tr');
        
        // Person name cell
        const nameCell = document.createElement('td');
        nameCell.className = 'person-cell';
        nameCell.textContent = person;
        row.appendChild(nameCell);
        
        // Space metric cells
        selectedSpaces.forEach(space => {
            const metric = metrics[person][space];
            const cell = createMetricCell(person, space, metric);
            row.appendChild(cell);
        });
        
        // Total cell
        const totalMetric = metrics[person]['_total'];
        const totalCell = createMetricCell(person, '_total', totalMetric);
        totalCell.className = 'metric-cell total-cell';
        row.appendChild(totalCell);
        
        tbody.appendChild(row);
    });
    table.appendChild(tbody);
    
    // Create footer with totals
    const tfoot = document.createElement('tfoot');
    const footerRow = document.createElement('tr');
    footerRow.appendChild(createHeaderCell('Total'));
    
    // Calculate column totals
    selectedSpaces.forEach(space => {
        let colAssigned = 0, colCompleted = 0, colGiven = 0;
        selectedPeople.forEach(person => {
            colAssigned += metrics[person][space].assigned;
            colCompleted += metrics[person][space].completed;
            colGiven += metrics[person][space].given;
        });
        const cell = createMetricCell('_all', space, {
            assigned: colAssigned,
            completed: colCompleted,
            given: colGiven
        });
        cell.className = 'metric-cell total-cell';
        footerRow.appendChild(cell);
    });
    
    // Grand total
    let grandAssigned = 0, grandCompleted = 0, grandGiven = 0;
    selectedPeople.forEach(person => {
        grandAssigned += metrics[person]['_total'].assigned;
        grandCompleted += metrics[person]['_total'].completed;
        grandGiven += metrics[person]['_total'].given;
    });
    const grandCell = createMetricCell('_all', '_total', {
        assigned: grandAssigned,
        completed: grandCompleted,
        given: grandGiven
    });
    grandCell.className = 'metric-cell total-cell grand-total';
    footerRow.appendChild(grandCell);
    
    tfoot.appendChild(footerRow);
    table.appendChild(tfoot);
}

// Helper function to create header cell
function createHeaderCell(text) {
    const th = document.createElement('th');
    th.textContent = text;
    return th;
}

// Helper function to create metric cell with clickable numbers
function createMetricCell(person, space, metric) {
    const cell = document.createElement('td');
    cell.className = 'metric-cell';
    
    // Create three clickable spans for each metric
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
    
    const givenSpan = document.createElement('span');
    givenSpan.className = 'metric-number given';
    givenSpan.textContent = metric.given;
    givenSpan.title = 'Tasks Given';
    if (metric.given > 0) {
        givenSpan.style.cursor = 'pointer';
        givenSpan.addEventListener('click', () => showTaskDetails(person, space, 'given'));
    }
    
    cell.appendChild(assignedSpan);
    cell.appendChild(document.createTextNode(' / '));
    cell.appendChild(completedSpan);
    cell.appendChild(document.createTextNode(' / '));
    cell.appendChild(givenSpan);
    
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
    } else if (metric === 'given') {
        filteredTasks = dashboardData.tasks.filter(task => 
            task.sender === person &&
            (space === '_total' || task.space_name === space)
        );
    }
    
    // Update details section
    const detailsSection = document.getElementById('task-details');
    const detailsTitle = document.getElementById('details-title');
    const detailsTable = document.getElementById('details-table');
    
    // Set title
    const metricText = metric === 'assigned' ? 'Assigned' : metric === 'completed' ? 'Completed' : 'Given';
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
        ['Task ID', 'Created', 'Assignee', 'Sender', 'Status', 'Space', 'First Message'].forEach(text => {
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
            
            // Sender
            const senderCell = document.createElement('td');
            senderCell.textContent = task.sender || 'N/A';
            row.appendChild(senderCell);
            
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

// Fetch data from API
async function fetchData() {
    const fetchBtn = document.getElementById('fetch-data-btn');
    const loadingMsg = document.getElementById('loading-message');
    const errorMsg = document.getElementById('error-message');
    const mainContent = document.getElementById('main-content');
    
    // Show loading state
    fetchBtn.disabled = true;
    loadingMsg.style.display = 'block';
    errorMsg.style.display = 'none';
    
    try {
        // Get the script path (e.g., /cgi-bin/tasks-reporter.cgi)
        const scriptPath = window.location.pathname.split('?')[0];
        const apiUrl = `${scriptPath}/api/fetch-data?period=${currentPeriod}`;
        const response = await fetch(apiUrl);
        
        if (!response.ok) {
            throw new Error(`Failed to fetch data: ${response.statusText} (${response.status})\nURL: ${apiUrl}`);
        }
        
        dashboardData = await response.json();
        
        // Hide loading, show main content
        loadingMsg.style.display = 'none';
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
});




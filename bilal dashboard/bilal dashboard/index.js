const websiteViewsChartCtx = document.getElementById('websiteViewsChart').getContext('2d');
new Chart(websiteViewsChartCtx, {
    type: 'bar',
    data: {
        labels: ['brand1', 'brand2', 'brand3', 'brand4', 'brand5'],
        datasets: [{
            label: 'Brands',
            data: [50, 30, 20, 40, 60, 80, 90],
            backgroundColor: '#4CAF50',
            borderWidth: 0
        }]
    },
    options: {
        responsive: true,
        plugins: {
            title: {
                display: false,
            },
            legend: {
                position: 'bottom'
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                ticks: {
                    color: '#666'
                },
                grid: {
                    color: '#ddd'
                }
            },
            x: {
                ticks: {
                    color: '#666'
                },
                grid: {
                    color: '#ddd'
                }
            }
        }
    }
});
function getChartData(index) {
    return {
        labels: allData[index].labels,
        datasets: [{
            label: 'Views',
            data: allData[index].data,
            backgroundColor: '#4CAF50',
            borderWidth: 0
        }]
    };
}


const dailySalesChartCtx = document.getElementById('dailySalesChart').getContext('2d');
new Chart(dailySalesChartCtx, {
    type: 'line',
    data: {
        labels: ['10-May', '15-May', '21-May', '19-May', '14-May', '30-May'],
        datasets: [{
            label: 'Time',
            data: [150, 200, 350, 400, 450, 300, 250, 320, 280, 260, 230, 300],
            borderColor: '#66BB6A',
            backgroundColor: 'rgba(102,187,106,0.2)',
            fill: true,
            tension: 0.4,
            borderWidth: 2
        }]
    },
    options: {
        responsive: true,
        plugins: {
            title: {
                display: false,
            },
            legend: {
                position: 'bottom'
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                ticks: {
                    color: '#666'
                },
                grid: {
                    color: '#ddd'
                }
            },
            x: {
                ticks: {
                    color: '#666'
                },
                grid: {
                    color: '#ddd'
                }
            }
        }
    }
});

const completedTasksChartCtx = document.getElementById('completedTasksChart').getContext('2d');
new Chart(completedTasksChartCtx, {
    type: 'line',
    data: {
        labels: ['brand1', 'brand2', 'brand3', 'brand4', 'brand5'],
        datasets: [{
            label: 'Cost',
            data: [0, 300, 400, 350, 420, 460],
            borderColor: '#26C6DA',
            backgroundColor: 'rgba(38,198,218,0.2)',
            fill: true,
            tension: 0.4,
            borderWidth: 2
        }]
    },
    options: {
        responsive: true,
        plugins: {
            title: {
                display: false,
            },
            legend: {
                position: 'bottom'
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                ticks: {
                    color: '#666'
                },
                grid: {
                    color: '#ddd'
                }
            },
            x: {
                ticks: {
                    color: '#666'
                },
                grid: {
                    color: '#ddd'
                }
            }
        }
    }
});

const toggleButton = document.getElementById('toggle-btn')
const sidebar = document.getElementById('sidebar')

function toggleSidebar(){
  sidebar.classList.toggle('close')
  toggleButton.classList.toggle('rotate')

  closeAllSubMenus()
}

function toggleSubMenu(button){

  if(!button.nextElementSibling.classList.contains('show')){
    closeAllSubMenus()
  }

  button.nextElementSibling.classList.toggle('show')
  button.classList.toggle('rotate')

  if(sidebar.classList.contains('close')){
    sidebar.classList.toggle('close')
    toggleButton.classList.toggle('rotate')
  }
}

function closeAllSubMenus(){
  Array.from(sidebar.getElementsByClassName('show')).forEach(ul => {
    ul.classList.remove('show')
    ul.previousElementSibling.classList.remove('rotate')
  })
}




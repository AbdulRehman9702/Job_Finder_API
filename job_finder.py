from fastapi import FastAPI
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from selenium.webdriver.chrome.options import Options
import requests
import time

app = FastAPI()

class JobSearchCriteria(BaseModel):
    position: str
    experience: str
    salary: str
    jobNature: str
    location: str
    skills: str

# ================= LinkedIn Logic (Unchanged) =================
def close_linkedin_modal(driver):
    selectors = [
        "button[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
        "button.modal__dismiss",
        "button[aria-label='Dismiss']"
    ]
    for selector in selectors:
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            btn.click()
            time.sleep(1)
            return True
        except TimeoutException:
            continue
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(1)
        return True
    except Exception:
        return False

def wait_until_all_jobs_loaded(driver, card_selector="div.job-search-card", max_wait=60):
    start_time = time.time()
    prev_count = -1
    while True:
        cards = driver.find_elements(By.CSS_SELECTOR, card_selector)
        curr_count = len(cards)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, card_selector)) > curr_count
            )
        except TimeoutException:
            pass
        if curr_count == prev_count or (time.time() - start_time) > max_wait:
            break
        prev_count = curr_count

def extract_linkedin_job_details(driver, job_link_element, criteria):
    try:
        driver.execute_script("arguments[0].scrollIntoView();", job_link_element)
        ActionChains(driver).move_to_element(job_link_element).perform()
        job_link_element.click()
    except ElementClickInterceptedException:
        driver.execute_script("window.scrollBy(0, 100);")
        time.sleep(1)
        job_link_element.click()

    try:
        WebDriverWait(driver, 55).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h2.top-card-layout__title"))
        )
    except TimeoutException:
        return None

    try:
        show_more_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button.show-more-less-html__button.show-more-less-button[aria-expanded='false']"))
        )
        driver.execute_script("arguments[0].scrollIntoView();", show_more_btn)
        show_more_btn.click()
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button.show-more-less-html__button.show-more-less-button[aria-expanded='true']"))
        )
    except TimeoutException:
        pass

    soup = BeautifulSoup(driver.page_source, "html.parser")
    title_tag = soup.find("h2", class_="top-card-layout__title")
    job_title = title_tag.get_text(strip=True) if title_tag else criteria.position
    company_tag = soup.find("span", class_="topcard__flavor")
    if not company_tag:
        company_tag = soup.find("a", class_="topcard__org-name-link topcard__flavor--black-link")
    company_name = company_tag.get_text(strip=True) if company_tag else ""
    location_tag = soup.find("span", class_="topcard__flavor topcard__flavor--bullet")
    location = location_tag.get_text(strip=True) if location_tag else criteria.location
    apply_link = job_link_element.get_attribute('href')
    description_tag = soup.find("div", class_="description__text description__text--rich")
    job_description = description_tag.get_text(separator="\n", strip=True) if description_tag else ""

    return {
        "job_title": job_title,
        "company": company_name,
        "experience": criteria.experience,
        "description": job_description,
        "jobNature": criteria.jobNature,
        "location": location,
        "salary": criteria.salary,
        "apply_link": apply_link,
    }

def scrape_linkedin_jobs(criteria):
    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--start-maximized")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    
    try:
        driver.get(f"https://www.linkedin.com/jobs/search/?keywords={criteria.position}&location={criteria.location}")
        close_linkedin_modal(driver)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.job-search-card"))
        )
        wait_until_all_jobs_loaded(driver)

        job_links = driver.find_elements(By.CSS_SELECTOR, "a.base-card__full-link")
        relevant_jobs = []
        for job_link in job_links:
            job_info = extract_linkedin_job_details(driver, job_link, criteria)
            if job_info:
                relevant_jobs.append(job_info)
            time.sleep(1)
        return relevant_jobs
    except Exception as e:
        print(f"LinkedIn Error: {e}")
        return []
    finally:
        driver.quit()

# ================= Updated Glassdoor Logic =================
def close_glassdoor_popups(driver):
    """Attempt to close any popups or modals that might interfere with clicking."""
    try:
        dismiss_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Dismiss')]")
        dismiss_btn.click()
        print("✅ Dismissed popup")
    except:
        pass
    try:
        close_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Close']")
        close_btn.click()
        print("✅ Closed modal")
    except:
        pass

def get_location_id(location):
    """Get Glassdoor location ID using their internal API"""
    try:
        formatted_location = location.replace(" ", "+").replace(",", "%2C")
        response = requests.get(
            f"https://www.glassdoor.com/findPopularLocationAjax.htm?term={formatted_location}"
        )
        data = response.json()
        return data[0]['locationId'] if data else '1127408'  # Default to Pakistan
    except Exception as e:
        print(f"Error getting location ID: {e}")
        return '1127408'

def construct_glassdoor_url(position, location):
    """Build URL with dynamic location ID"""
    position_slug = position.replace(" ", "-")
    location_slug = location.replace(" ", "-").replace(",", "-")
    location_id = get_location_id(location)
    
    return f"https://www.glassdoor.com/Job/{location_slug}-{position_slug}-jobs-SRCH_IL.0,{len(location_slug)}_IC{location_id}_KO{len(location_slug)+1},{len(location_slug)+1+len(position_slug)}.htm"

def scrape_glassdoor_jobs(criteria):
    options = Options()
    options.add_experimental_option("detach", True)
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--start-maximized")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    job_data = []

    try:
        url = construct_glassdoor_url(criteria.position, criteria.location)
        driver.get(url)
        print(f"✅ Opened Glassdoor URL: {url}")

        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button#onetrust-accept-btn-handler"))
            ).click()
            print("✅ Accepted cookies")
        except Exception:
            pass

        job_list_container = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.jobsList, ul[aria-label='Jobs List']"))
        )
        print("✅ Job listings container loaded")

        last_job_count = 0
        current_job_count = len(driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']"))

        while current_job_count > last_job_count:
            last_job_count = current_job_count
            driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight)", job_list_container)
            time.sleep(2)

            try:
                show_more_jobs_btn = driver.find_element(By.CSS_SELECTOR, "button.jobsearch-LoadMoreJobs, button[data-test='load-more-jobs']")
                if show_more_jobs_btn.is_displayed():
                    show_more_jobs_btn.click()
                    print("✅ Clicked 'Show More' button")
                    time.sleep(3)
            except Exception:
                pass

            current_job_count = len(driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']"))

        print(f"✅ Loaded total {current_job_count} job cards")

        job_listings = driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']")

        for index, job_card in enumerate(job_listings):
            print(f"\n📄 Processing job {index+1}/{len(job_listings)}")
            try:
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", job_card)
                time.sleep(1)
                try:
                    job_title_link = job_card.find_element(By.CSS_SELECTOR, "a[data-test='job-title'], a.jobTitle, a.jobCard_jobTitle")
                    job_title_link.click()
                except:
                    job_card.click()

                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.TwoColumnLayout_columnRight__GRvqO, div.TwoColumnLayout_jobDetailsContainer__qyvJZ"))
                )
                time.sleep(2)

                close_glassdoor_popups(driver)

                try:
                    show_more_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test='show-more-cta'][aria-expanded='false']"))
                    )
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", show_more_btn)
                    time.sleep(1)
                    try:
                        ActionChains(driver).move_to_element(show_more_btn).click().perform()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", show_more_btn)
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-test='show-more-cta'][aria-expanded='true']"))
                    )
                    time.sleep(2)
                except:
                    pass

                # Extract job info
                try:
                    job_title = driver.find_element(By.CSS_SELECTOR, "h1[id^='jd-job-title']").text.strip()
                except:
                    job_title = criteria.position


                try:
                    company = driver.find_element(By.CSS_SELECTOR, "h4[class*='heading_Subhead']").text.strip()
                except:
                    company = "N/A"


                try:
                    location = driver.find_element(By.CSS_SELECTOR, "div[data-test='location'], div.companyLocation").text.strip()
                except:
                    location = criteria.location

                try:
                    salary = driver.find_element(By.CSS_SELECTOR, "div[data-test='detailSalary'], div.salaryEstimate").text.strip()
                except:
                    salary = criteria.salary

                try:
                    description = driver.find_element(By.CSS_SELECTOR, "div.JobDetails_jobDescription__uW_fK > div").text.strip()
                except:
                    description = ""

                apply_link = driver.current_url

                job_info = {
                    "job_title": job_title,
                    "company": company,
                    "experience": criteria.experience,
                    "jobNature": criteria.jobNature,
                    "location": location,
                    "salary": salary,
                    "description": description,
                    "apply_link": apply_link
                }

                print(f"✅ Collected: {job_title} at {company}")
                job_data.append(job_info)

            except Exception as e:
                print(f"❌ Error processing job {index+1}: {e}")
                continue

        return job_data

    except Exception as e:
        print(f"❌ Glassdoor scraping failed: {e}")
        return []


@app.post("/search_jobs")
def search_jobs(criteria: JobSearchCriteria):
    with ThreadPoolExecutor() as executor:
        future_linkedin = executor.submit(scrape_linkedin_jobs, criteria)
        future_glassdoor = executor.submit(scrape_glassdoor_jobs, criteria)

        linkedin_results = future_linkedin.result()
        glassdoor_results = future_glassdoor.result()

    # Optionally tag each job with the source
    for job in linkedin_results:
        job['source'] = "LinkedIn"
    for job in glassdoor_results:
        job['source'] = "Glassdoor"

    merged_results = linkedin_results + glassdoor_results
    return {"total_results": len(merged_results), "jobs": merged_results}
    # return {"total_results": len(linkedin_results), "jobs": linkedin_results}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


#############################################
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from selenium.webdriver.chrome.options import Options
import requests
import time
from scipy.spatial.distance import cosine

app = FastAPI()

class JobSearchCriteria(BaseModel):
    position: str
    experience: str
    salary: str
    jobNature: str
    location: str
    skills: str

# Initialize LLM model
model = SentenceTransformer('all-MiniLM-L6-v2')

def close_linkedin_modal(driver):
    selectors = [
        "button[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
        "button.modal__dismiss",
        "button[aria-label='Dismiss']"
    ]
    for selector in selectors:
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            btn.click()
            time.sleep(1)
            return True
        except TimeoutException:
            continue
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(1)
        return True
    except Exception:
        return False

def wait_until_all_jobs_loaded(driver, card_selector="div.job-search-card", max_wait=60):
    start_time = time.time()
    prev_count = -1
    while True:
        cards = driver.find_elements(By.CSS_SELECTOR, card_selector)
        curr_count = len(cards)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, card_selector)) > curr_count
            )
        except TimeoutException:
            pass
        if curr_count == prev_count or (time.time() - start_time) > max_wait:
            break
        prev_count = curr_count

def extract_linkedin_job_details(driver, job_link_element, criteria):
    try:
        driver.execute_script("arguments[0].scrollIntoView();", job_link_element)
        ActionChains(driver).move_to_element(job_link_element).perform()
        job_link_element.click()
    except ElementClickInterceptedException:
        driver.execute_script("window.scrollBy(0, 100);")
        time.sleep(1)
        job_link_element.click()

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h2.top-card-layout__title"))
        )
    except TimeoutException:
        return None

    try:
        show_more_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button.show-more-less-html__button.show-more-less-button[aria-expanded='false']"))
        )
        driver.execute_script("arguments[0].scrollIntoView();", show_more_btn)
        show_more_btn.click()
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button.show-more-less-html__button.show-more-less-button[aria-expanded='true']"))
        )
    except TimeoutException:
        pass

    soup = BeautifulSoup(driver.page_source, "html.parser")
    title_tag = soup.find("h2", class_="top-card-layout__title")
    job_title = title_tag.get_text(strip=True) if title_tag else criteria.position
    company_tag = soup.find("span", class_="topcard__flavor")
    if not company_tag:
        company_tag = soup.find("a", class_="topcard__org-name-link topcard__flavor--black-link")
    company_name = company_tag.get_text(strip=True) if company_tag else ""
    location_tag = soup.find("span", class_="topcard__flavor topcard__flavor--bullet")
    location = location_tag.get_text(strip=True) if location_tag else criteria.location
    apply_link = job_link_element.get_attribute('href')
    description_tag = soup.find("div", class_="description__text description__text--rich")
    job_description = description_tag.get_text(separator="\n", strip=True) if description_tag else ""

    return {
        "job_title": job_title,
        "company": company_name,
        "experience": criteria.experience,
        "description": job_description,
        "jobNature": criteria.jobNature,
        "location": location,
        "salary": criteria.salary,
        "apply_link": apply_link,
    }

def scrape_linkedin_jobs(criteria):
    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--start-maximized")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    
    try:
        driver.get(f"https://www.linkedin.com/jobs/search/?keywords={criteria.position}&location={criteria.location}")
        close_linkedin_modal(driver)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.job-search-card"))
        )
        wait_until_all_jobs_loaded(driver)

        job_links = driver.find_elements(By.CSS_SELECTOR, "a.base-card__full-link")
        relevant_jobs = []
        for job_link in job_links:
            job_info = extract_linkedin_job_details(driver, job_link, criteria)
            if job_info:
                relevant_jobs.append(job_info)
            time.sleep(1)
        return relevant_jobs
    except Exception as e:
        print(f"LinkedIn Error: {e}")
        return []
    finally:
        driver.quit()

def close_glassdoor_popups(driver):
    try:
        dismiss_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Dismiss')]")
        dismiss_btn.click()
    except:
        pass
    try:
        close_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Close']")
        close_btn.click()
    except:
        pass

def get_location_id(location):
    try:
        formatted_location = location.replace(" ", "+").replace(",", "%2C")
        response = requests.get(
            f"https://www.glassdoor.com/findPopularLocationAjax.htm?term={formatted_location}"
        )
        data = response.json()
        return data[0]['locationId'] if data else '1127408'
    except Exception as e:
        print(f"Error getting location ID: {e}")
        return '1127408'

def construct_glassdoor_url(position, location):
    position_slug = position.replace(" ", "-")
    location_slug = location.replace(" ", "-").replace(",", "-")
    location_id = get_location_id(location)
    
    return f"https://www.glassdoor.com/Job/{location_slug}-{position_slug}-jobs-SRCH_IL.0,{len(location_slug)}_IC{location_id}_KO{len(location_slug)+1},{len(location_slug)+1+len(position_slug)}.htm"

def scrape_glassdoor_jobs(criteria):
    options = Options()
    options.add_experimental_option("detach", True)
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--start-maximized")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    job_data = []

    try:
        url = construct_glassdoor_url(criteria.position, criteria.location)
        driver.get(url)

        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button#onetrust-accept-btn-handler"))
            ).click()
        except Exception:
            pass

        job_list_container = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.jobsList, ul[aria-label='Jobs List']"))
        )

        last_job_count = 0
        current_job_count = len(driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']"))

        while current_job_count > last_job_count:
            last_job_count = current_job_count
            driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight)", job_list_container)
            time.sleep(2)

            try:
                show_more_jobs_btn = driver.find_element(By.CSS_SELECTOR, "button.jobsearch-LoadMoreJobs, button[data-test='load-more-jobs']")
                if show_more_jobs_btn.is_displayed():
                    show_more_jobs_btn.click()
                    time.sleep(3)
            except Exception:
                pass

            current_job_count = len(driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']"))

        job_listings = driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']")

        for job_card in job_listings:
            try:
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", job_card)
                time.sleep(1)
                try:
                    job_title_link = job_card.find_element(By.CSS_SELECTOR, "a[data-test='job-title'], a.jobTitle, a.jobCard_jobTitle")
                    job_title_link.click()
                except:
                    job_card.click()

                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.TwoColumnLayout_columnRight__GRvqO, div.TwoColumnLayout_jobDetailsContainer__qyvJZ"))
                )
                time.sleep(2)

                close_glassdoor_popups(driver)

                try:
                    show_more_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test='show-more-cta'][aria-expanded='false']"))
                    )
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", show_more_btn)
                    time.sleep(1)
                    try:
                        ActionChains(driver).move_to_element(show_more_btn).click().perform()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", show_more_btn)
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-test='show-more-cta'][aria-expanded='true']"))
                    )
                    time.sleep(2)
                except:
                    pass

                try:
                    job_title = driver.find_element(By.CSS_SELECTOR, "h1[id^='jd-job-title']").text.strip()
                except:
                    job_title = criteria.position

                try:
                    company = driver.find_element(By.CSS_SELECTOR, "h4[class*='heading_Subhead']").text.strip()
                except:
                    company = "N/A"

                try:
                    location = driver.find_element(By.CSS_SELECTOR, "div[data-test='location'], div.companyLocation").text.strip()
                except:
                    location = criteria.location

                try:
                    salary = driver.find_element(By.CSS_SELECTOR, "div[data-test='detailSalary'], div.salaryEstimate").text.strip()
                except:
                    salary = criteria.salary

                try:
                    description = driver.find_element(By.CSS_SELECTOR, "div.JobDetails_jobDescription__uW_fK > div").text.strip()
                except:
                    description = ""

                apply_link = driver.current_url

                job_info = {
                    "job_title": job_title,
                    "company": company,
                    "experience": criteria.experience,
                    "jobNature": criteria.jobNature,
                    "location": location,
                    "salary": salary,
                    "description": description,
                    "apply_link": apply_link
                }

                job_data.append(job_info)

            except Exception:
                continue

        return job_data

    except Exception as e:
        print(f"Glassdoor scraping failed: {e}")
        return []

    finally:
        driver.quit()

def is_relevant(job_description, job_title, user_criteria):
    job_text = f"{job_title}. {job_description}"
    query = f"{user_criteria.position} requiring skills: {user_criteria.skills}"

    query_embedding = model.encode(query)
    desc_embedding = model.encode(job_text)
    similarity = 1 - cosine(query_embedding, desc_embedding)

    return similarity > 0.5 or user_criteria.position.lower() in job_title.lower()

@app.post("/search_jobs")
def search_jobs(criteria: JobSearchCriteria):
    with ThreadPoolExecutor() as executor:
        future_linkedin = executor.submit(scrape_linkedin_jobs, criteria)
        future_glassdoor = executor.submit(scrape_glassdoor_jobs, criteria)

        linkedin_results = future_linkedin.result()
        glassdoor_results = future_glassdoor.result()

    all_jobs = linkedin_results + glassdoor_results

    relevant_jobs = []
    for job in all_jobs:
        if is_relevant(job.get("description", ""), job.get("job_title", ""), criteria):
            relevant_jobs.append({
                "job_title": job.get("job_title", ""),
                "company": job.get("company", ""),
                "experience": job.get("experience", ""),
                "jobNature": job.get("jobNature", ""),
                "location": job.get("location", ""),
                "salary": job.get("salary", ""),
                "apply_link": job.get("apply_link", "")
            })

    return {"relevant_jobs": relevant_jobs}

#############################################
##With Meta Llama 3.2 1B Parameter
#############################################
# from fastapi import FastAPI
# from pydantic import BaseModel
# from selenium import webdriver
# from selenium.webdriver.chrome.service import Service
# from selenium.webdriver.common.by import By
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
# from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
# from selenium.webdriver.common.action_chains import ActionChains
# from selenium.webdriver.common.keys import Keys
# from webdriver_manager.chrome import ChromeDriverManager
# from bs4 import BeautifulSoup
# from urllib.parse import urlencode
# from concurrent.futures import ThreadPoolExecutor
# from selenium.webdriver.chrome.options import Options
# import requests
# import time
# import torch
# import torch.nn.functional as F
# import numpy as np
# from transformers import AutoTokenizer, AutoModelForCausalLM

# app = FastAPI()

# class JobSearchCriteria(BaseModel):
#     position: str
#     experience: str
#     salary: str
#     jobNature: str
#     location: str
#     skills: str

# class LLaMAEmbedder:
#     def __init__(self, model_name_or_path: str = "meta-llama/Llama-3.2-1B", device: str = None):
#         self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
#         self.tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, use_auth_token=True)
#         self.model = AutoModelForCausalLM.from_pretrained(model_name_or_path, use_auth_token=True).to(self.device)
#         self.model.eval()

#     def embed_text(self, text: str):
#         inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
#         inputs = {k: v.to(self.device) for k, v in inputs.items()}
#         with torch.no_grad():
#             outputs = self.model.base_model(**inputs, output_hidden_states=True)
#             last_hidden_state = outputs.hidden_states[-1]
#             attention_mask = inputs["attention_mask"].unsqueeze(-1)
#             masked_hidden = last_hidden_state * attention_mask
#             summed = masked_hidden.sum(dim=1)
#             counts = attention_mask.sum(dim=1)
#             embedding = summed / counts
#             embedding = F.normalize(embedding, p=2, dim=1)
#             return embedding.cpu().numpy()[0]

# llama_embedder = LLaMAEmbedder()

# def is_relevant(job_description, job_title, user_criteria):
#     job_text = f"{job_title}. {job_description}"
#     query = f"{user_criteria.position} requiring skills: {user_criteria.skills}"

#     query_embedding = llama_embedder.embed_text(query)
#     desc_embedding = llama_embedder.embed_text(job_text)

#     similarity = np.dot(query_embedding, desc_embedding) / (np.linalg.norm(query_embedding) * np.linalg.norm(desc_embedding))

#     return similarity > 0.5 or user_criteria.position.lower() in job_title.lower()

# def close_linkedin_modal(driver):
#     selectors = [
#         "button[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
#         "button.modal__dismiss",
#         "button[aria-label='Dismiss']"
#     ]
#     for selector in selectors:
#         try:
#             btn = WebDriverWait(driver, 10).until(
#                 EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
#             )
#             btn.click()
#             time.sleep(1)
#             return True
#         except TimeoutException:
#             continue
#     try:
#         ActionChains(driver).send_keys(Keys.ESCAPE).perform()
#         time.sleep(1)
#         return True
#     except Exception:
#         return False

# def wait_until_all_jobs_loaded(driver, card_selector="div.job-search-card", max_wait=60):
#     start_time = time.time()
#     prev_count = -1
#     while True:
#         cards = driver.find_elements(By.CSS_SELECTOR, card_selector)
#         curr_count = len(cards)
#         driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
#         try:
#             WebDriverWait(driver, 10).until(
#                 lambda d: len(d.find_elements(By.CSS_SELECTOR, card_selector)) > curr_count
#             )
#         except TimeoutException:
#             pass
#         if curr_count == prev_count or (time.time() - start_time) > max_wait:
#             break
#         prev_count = curr_count

# def extract_linkedin_job_details(driver, job_link_element, criteria):
#     try:
#         driver.execute_script("arguments[0].scrollIntoView();", job_link_element)
#         ActionChains(driver).move_to_element(job_link_element).perform()
#         job_link_element.click()
#     except ElementClickInterceptedException:
#         driver.execute_script("window.scrollBy(0, 100);")
#         time.sleep(1)
#         job_link_element.click()

#     try:
#         WebDriverWait(driver, 15).until(
#             EC.presence_of_element_located((By.CSS_SELECTOR, "h2.top-card-layout__title"))
#         )
#     except TimeoutException:
#         return None

#     try:
#         show_more_btn = WebDriverWait(driver, 10).until(
#             EC.element_to_be_clickable(
#                 (By.CSS_SELECTOR, "button.show-more-less-html__button.show-more-less-button[aria-expanded='false']"))
#         )
#         driver.execute_script("arguments[0].scrollIntoView();", show_more_btn)
#         show_more_btn.click()
#         WebDriverWait(driver, 20).until(
#             EC.presence_of_element_located(
#                 (By.CSS_SELECTOR, "button.show-more-less-html__button.show-more-less-button[aria-expanded='true']"))
#         )
#     except TimeoutException:
#         pass

#     soup = BeautifulSoup(driver.page_source, "html.parser")
#     title_tag = soup.find("h2", class_="top-card-layout__title")
#     job_title = title_tag.get_text(strip=True) if title_tag else criteria.position
#     company_tag = soup.find("span", class_="topcard__flavor")
#     if not company_tag:
#         company_tag = soup.find("a", class_="topcard__org-name-link topcard__flavor--black-link")
#     company_name = company_tag.get_text(strip=True) if company_tag else ""
#     location_tag = soup.find("span", class_="topcard__flavor topcard__flavor--bullet")
#     location = location_tag.get_text(strip=True) if location_tag else criteria.location
#     apply_link = job_link_element.get_attribute('href')
#     description_tag = soup.find("div", class_="description__text description__text--rich")
#     job_description = description_tag.get_text(separator="\n", strip=True) if description_tag else ""

#     return {
#         "job_title": job_title,
#         "company": company_name,
#         "experience": criteria.experience,
#         "description": job_description,
#         "jobNature": criteria.jobNature,
#         "location": location,
#         "salary": criteria.salary,
#         "apply_link": apply_link,
#     }

# def scrape_linkedin_jobs(criteria):
#     options = Options()
#     options.add_argument("--disable-notifications")
#     options.add_argument("--disable-popup-blocking")
#     options.add_argument("--start-maximized")
#     options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
#     driver = webdriver.Chrome(
#         service=Service(ChromeDriverManager().install()),
#         options=options
#     )
    
#     try:
#         driver.get(f"https://www.linkedin.com/jobs/search/?keywords={criteria.position}&location={criteria.location}")
#         close_linkedin_modal(driver)
#         WebDriverWait(driver, 20).until(
#             EC.presence_of_element_located((By.CSS_SELECTOR, "div.job-search-card"))
#         )
#         wait_until_all_jobs_loaded(driver)

#         job_links = driver.find_elements(By.CSS_SELECTOR, "a.base-card__full-link")
#         relevant_jobs = []
#         for job_link in job_links:
#             job_info = extract_linkedin_job_details(driver, job_link, criteria)
#             if job_info:
#                 relevant_jobs.append(job_info)
#             time.sleep(1)
#         return relevant_jobs
#     except Exception as e:
#         print(f"LinkedIn Error: {e}")
#         return []
#     finally:
#         driver.quit()

# def close_glassdoor_popups(driver):
#     try:
#         dismiss_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Dismiss')]")
#         dismiss_btn.click()
#     except:
#         pass
#     try:
#         close_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Close']")
#         close_btn.click()
#     except:
#         pass

# def get_location_id(location):
#     try:
#         formatted_location = location.replace(" ", "+").replace(",", "%2C")
#         response = requests.get(
#             f"https://www.glassdoor.com/findPopularLocationAjax.htm?term={formatted_location}"
#         )
#         data = response.json()
#         return data[0]['locationId'] if data else '1127408'
#     except Exception as e:
#         print(f"Error getting location ID: {e}")
#         return '1127408'

# def construct_glassdoor_url(position, location):
#     position_slug = position.replace(" ", "-")
#     location_slug = location.replace(" ", "-").replace(",", "-")
#     location_id = get_location_id(location)
    
#     return f"https://www.glassdoor.com/Job/{location_slug}-{position_slug}-jobs-SRCH_IL.0,{len(location_slug)}_IC{location_id}_KO{len(location_slug)+1},{len(location_slug)+1+len(position_slug)}.htm"

# def scrape_glassdoor_jobs(criteria):
#     options = Options()
#     options.add_experimental_option("detach", True)
#     options.add_argument("--disable-notifications")
#     options.add_argument("--disable-popup-blocking")
#     options.add_argument("--start-maximized")
#     options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

#     driver = webdriver.Chrome(
#         service=Service(ChromeDriverManager().install()),
#         options=options
#     )

#     job_data = []

#     try:
#         url = construct_glassdoor_url(criteria.position, criteria.location)
#         driver.get(url)

#         try:
#             WebDriverWait(driver, 10).until(
#                 EC.element_to_be_clickable((By.CSS_SELECTOR, "button#onetrust-accept-btn-handler"))
#             ).click()
#         except Exception:
#             pass

#         job_list_container = WebDriverWait(driver, 30).until(
#             EC.presence_of_element_located((By.CSS_SELECTOR, "ul.jobsList, ul[aria-label='Jobs List']"))
#         )

#         last_job_count = 0
#         current_job_count = len(driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']"))

#         while current_job_count > last_job_count:
#             last_job_count = current_job_count
#             driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight)", job_list_container)
#             time.sleep(2)

#             try:
#                 show_more_jobs_btn = driver.find_element(By.CSS_SELECTOR, "button.jobsearch-LoadMoreJobs, button[data-test='load-more-jobs']")
#                 if show_more_jobs_btn.is_displayed():
#                     show_more_jobs_btn.click()
#                     time.sleep(3)
#             except Exception:
#                 pass

#             current_job_count = len(driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']"))

#         job_listings = driver.find_elements(By.CSS_SELECTOR, "li.react-job-listing, li[data-test='jobListing']")

#         for job_card in job_listings:
#             try:
#                 driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", job_card)
#                 time.sleep(1)
#                 try:
#                     job_title_link = job_card.find_element(By.CSS_SELECTOR, "a[data-test='job-title'], a.jobTitle, a.jobCard_jobTitle")
#                     job_title_link.click()
#                 except:
#                     job_card.click()

#                 WebDriverWait(driver, 10).until(
#                     EC.presence_of_element_located((By.CSS_SELECTOR, "div.TwoColumnLayout_columnRight__GRvqO, div.TwoColumnLayout_jobDetailsContainer__qyvJZ"))
#                 )
#                 time.sleep(2)

#                 close_glassdoor_popups(driver)

#                 try:
#                     show_more_btn = WebDriverWait(driver, 5).until(
#                         EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test='show-more-cta'][aria-expanded='false']"))
#                     )
#                     driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", show_more_btn)
#                     time.sleep(1)
#                     try:
#                         ActionChains(driver).move_to_element(show_more_btn).click().perform()
#                     except ElementClickInterceptedException:
#                         driver.execute_script("arguments[0].click();", show_more_btn)
#                     WebDriverWait(driver, 5).until(
#                         EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-test='show-more-cta'][aria-expanded='true']"))
#                     )
#                     time.sleep(2)
#                 except:
#                     pass

#                 try:
#                     job_title = driver.find_element(By.CSS_SELECTOR, "h1[id^='jd-job-title']").text.strip()
#                 except:
#                     job_title = criteria.position

#                 try:
#                     company = driver.find_element(By.CSS_SELECTOR, "h4[class*='heading_Subhead']").text.strip()
#                 except:
#                     company = "N/A"

#                 try:
#                     location = driver.find_element(By.CSS_SELECTOR, "div[data-test='location'], div.companyLocation").text.strip()
#                 except:
#                     location = criteria.location

#                 try:
#                     salary = driver.find_element(By.CSS_SELECTOR, "div[data-test='detailSalary'], div.salaryEstimate").text.strip()
#                 except:
#                     salary = criteria.salary

#                 try:
#                     description = driver.find_element(By.CSS_SELECTOR, "div.JobDetails_jobDescription__uW_fK > div").text.strip()
#                 except:
#                     description = ""

#                 apply_link = driver.current_url

#                 job_info = {
#                     "job_title": job_title,
#                     "company": company,
#                     "experience": criteria.experience,
#                     "jobNature": criteria.jobNature,
#                     "location": location,
#                     "salary": salary,
#                     "description": description,
#                     "apply_link": apply_link
#                 }

#                 job_data.append(job_info)

#             except Exception:
#                 continue

#         return job_data

#     except Exception as e:
#         print(f"Glassdoor scraping failed: {e}")
#         return []

#     finally:
#         driver.quit()

# @app.post("/search_jobs")
# def search_jobs(criteria: JobSearchCriteria):
#     with ThreadPoolExecutor() as executor:
#         future_linkedin = executor.submit(scrape_linkedin_jobs, criteria)
#         future_glassdoor = executor.submit(scrape_glassdoor_jobs, criteria)

#         linkedin_results = future_linkedin.result()
#         glassdoor_results = future_glassdoor.result()

#     all_jobs = linkedin_results + glassdoor_results

#     relevant_jobs = []
#     for job in all_jobs:
#         if is_relevant(job.get("description", ""), job.get("job_title", ""), criteria):
#             relevant_jobs.append({
#                 "job_title": job.get("job_title", ""),
#                 "company": job.get("company", ""),
#                 "experience": job.get("experience", ""),
#                 "jobNature": job.get("jobNature", ""),
#                 "location": job.get("location", ""),
#                 "salary": job.get("salary", ""),
#                 "apply_link": job.get("apply_link", "")
#             })

#     return {"relevant_jobs": relevant_jobs}

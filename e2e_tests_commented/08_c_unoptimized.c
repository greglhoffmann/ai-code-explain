#include <stdio.h>
#include <string.h>

// O(n) membership check used inside another O(n) loop below => O(n^2) unique counting.
int contains(int arr[], int size, int target) {
    for (int i = 0; i < size; i++) {
        if (arr[i] == target) {
            return 1;
        }
    }
    return 0;
}

// Unoptimized bubble sort: always runs full passes, no early-exit flag.
void bubble_sort(int arr[], int size) {
    for (int i = 0; i < size; i++) {
        for (int j = 0; j < size - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int tmp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = tmp;
            }
        }
    }
}

// Uses fixed-size buffer + repeated linear scans instead of hash/set structure.
int count_unique(int arr[], int size) {
    int seen[1024];
    int seen_count = 0;
    int unique = 0;

    for (int i = 0; i < size; i++) {
        if (!contains(seen, seen_count, arr[i])) {
            seen[seen_count++] = arr[i];
            unique++;
        }
    }

    return unique;
}

int main(void) {
    int nums[] = {9, 3, 7, 3, 2, 1, 9, 8, 2};
    int n = (int)(sizeof(nums) / sizeof(nums[0]));

    bubble_sort(nums, n);
    printf("Unique count: %d\n", count_unique(nums, n));

    return 0;
}
